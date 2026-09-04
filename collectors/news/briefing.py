# 그날 뉴스에서 무엇이 논의됐는지 한 통으로 묶어 텔레그램으로 보내는 일 1회 배치

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import anthropic

from common.config import load_config
from common.db.conn import connect, transaction
from common.db.events import log_event
from common.db.heartbeat import run_with_heartbeat
from common.db.keywords import DailyKeyword, daily_ranked
from common.db.messages import analyzed_count, samples_on_day
from common.db.usage import record_usage, today_cost
from common.env import require_env
from common.notify.base import Level, Notifier
from common.notify.telegram import TelegramNotifier

from .analyze import workspace_header
from .llm import cost_of

logger = logging.getLogger(__name__)

PROCESS = "news_briefing"
PROVIDER = "anthropic"

# 집계와 같은 달력일(KST) 기준이다. 거래일이 아니다
SEOUL = ZoneInfo("Asia/Seoul")


# **아는 것을 보태지 말라**가 이 프롬프트의 전부다. 요약기가 사실을 지어내면
# 브리핑을 읽고 판단할 수 없다. 원문에 없으면 안 쓰는 편이 낫다
SYSTEM = """너는 그날 한국 주식 시장 뉴스에서 **무엇이 논의됐는지** 정리한다.

받는 것
- 그날 평소보다 많이 나온 테마 키워드와 그 배수
- 각 키워드가 나온 원문 몇 줄

하는 일
1. 키워드를 섹터로 묶는다. **원문의 논조**로 강세와 약세를 가른다
2. 그날 눈여겨볼 것을 최대 3가지 쓴다

지키는 것
- **원문에 없는 사실을 쓰지 않는다.** 알고 있는 것을 보태지 않는다
- 매수나 매도를 권하지 않는다. 무엇이 논의됐는지만 쓴다
- 종목명은 원문에 나온 것만 쓴다
- 한 항목은 한 줄. 길어도 두 줄이다
- **근거가 얇으면 적게 쓴다.** 3가지를 억지로 채우지 않는다
- 강세도 약세도 분명하지 않으면 그 배열을 비운다
- 한국어로 쓴다. 문장은 마침표로 끝낸다"""


SCHEMA = {
    "type": "object",
    "properties": {
        "strong": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sector": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["sector", "note"],
                "additionalProperties": False,
            },
        },
        "weak": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sector": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["sector", "note"],
                "additionalProperties": False,
            },
        },
        "points": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["strong", "weak", "points"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Material:
    """브리핑의 재료. DB 에서 읽은 것만 담는다."""

    day: date
    ranked: list[DailyKeyword]
    samples: list[tuple[str, list[str]]]
    message_count: int


@dataclass(frozen=True)
class Briefing:
    """LLM 이 쓴 부분. 목록은 DB 가 이미 가지고 있어 여기 없다."""

    strong: list[tuple[str, str]]
    weak: list[tuple[str, str]]
    points: list[str]
    input_tokens: int
    output_tokens: int


def gather(cur, day: date, params: dict[str, Any]) -> Material:
    """그날 재료를 모은다. 상위 몇 개만 표본을 붙인다."""
    ranked = daily_ranked(cur, day, params["top_keywords"])
    samples = [
        (row.term, samples_on_day(cur, row.keyword_id, day, params["sample_messages"]))
        for row in ranked[: params["sample_keywords"]]
    ]
    return Material(
        day=day,
        ranked=ranked,
        samples=[(term, texts) for term, texts in samples if texts],
        message_count=analyzed_count(cur, day),
    )


def build_prompt(material: Material, sample_chars: int) -> str:
    """키워드마다 배수와 원문 표본을 붙인다. 강세·약세는 원문에만 있다."""
    blocks = []
    for term, texts in material.samples:
        row = next(r for r in material.ranked if r.term == term)
        head = f"## {term} — {row.mention_count}회{_ratio(row)}"
        body = "\n".join(f"- {text[:sample_chars]}" for text in texts)
        blocks.append(f"{head}\n{body}")
    return f"{material.day} 뉴스\n\n" + "\n\n".join(blocks)


def write(
    client: Any, material: Material, params: dict[str, Any], model: str
) -> Briefing:
    """LLM 에 한 번 묻는다. 브리핑은 하루 한 통이라 배치가 없다."""
    response = client.messages.create(
        model=model,
        max_tokens=params["max_output_tokens"],
        system=SYSTEM,
        messages=[
            {
                "role": "user",
                "content": build_prompt(material, params["sample_chars"]),
            }
        ],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )
    text = next(block.text for block in response.content if block.type == "text")
    parsed = json.loads(text)
    return Briefing(
        strong=_sectors(parsed.get("strong")),
        weak=_sectors(parsed.get("weak")),
        points=[str(p).strip() for p in (parsed.get("points") or []) if str(p).strip()],
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )


def render(material: Material, briefing: Briefing, max_chars: int) -> str:
    """평문으로 만든다. 서식을 쓰면 종목명의 기호가 발송을 400 으로 만든다."""
    lines = [f"{material.day} ({'월화수목금토일'[material.day.weekday()]})", ""]

    lines.append("[ 오늘 뜬 테마 ]")
    for index, row in enumerate(material.ranked, 1):
        lines.append(f"{index:2d}. {row.term} {row.mention_count}회{_ratio(row)}")

    for title, sectors in (("강세", briefing.strong), ("약세", briefing.weak)):
        if sectors:
            lines += ["", f"[ {title} ]"]
            lines += [f"- {sector}: {note}" for sector, note in sectors]

    if briefing.points:
        lines += ["", "[ 볼 만한 것 ]"]
        lines += [f"{index}. {p}" for index, p in enumerate(briefing.points, 1)]

    lines += ["", f"분석 원문 {material.message_count}건"]
    return "\n".join(lines)[:max_chars]


def main(argv: list[str], notifier: Notifier | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    config = load_config("collect")["news"]["briefing"]
    limits = load_config("limits")["llm"]
    today = datetime.now(SEOUL).date()
    day = date.fromisoformat(argv[1]) if len(argv) > 1 else today

    with connect() as conn, transaction(conn) as cur:
        material = gather(cur, day, config)
        spent = today_cost(cur, PROVIDER, today)

    # 조용한 날은 보내지 않는다. 빈 브리핑이 매일 오면 아무도 안 읽는다
    if not material.samples:
        print(f"{day} 재료가 없습니다. 보내지 않습니다.")
        return 0

    cap = Decimal(str(limits["daily_cost_usd"]))
    if spent >= cap:
        # 분석 배치가 상한을 다 썼다. 브리핑 때문에 상한을 넘기지 않는다
        _log(day, "WARN", "상한 초과로 건너뜀", {"spent": str(spent)})
        print(f"당일 LLM 비용 {spent} 가 상한 {cap} 이상입니다. 보내지 않습니다.")
        return 0

    try:
        client = anthropic.Anthropic(
            api_key=require_env("ANTHROPIC_API_KEY"),
            default_headers=workspace_header(),
        )
        briefing = write(client, material, config, limits["model"])
    except Exception:
        # 브리핑 실패가 다른 수집기를 멈추면 안 된다. 남기고 끝낸다
        logger.exception("브리핑 생성 실패")
        _log(day, "ERROR", "생성 실패", {})
        return 1

    with connect() as conn, transaction(conn) as cur:
        record_usage(
            cur,
            today,
            PROVIDER,
            limits["model"],
            input_tokens=briefing.input_tokens,
            output_tokens=briefing.output_tokens,
            cost_usd=cost_of(briefing.input_tokens, briefing.output_tokens, limits),
        )

    body = render(material, briefing, config["max_body_chars"])
    sent = _send(body, notifier)
    _log(
        day,
        "INFO" if sent else "ERROR",
        "일일 브리핑",
        {
            "keywords": len(material.ranked),
            "messages": material.message_count,
            "sent": sent,
            # api_usage 의 endpoint 는 모델명이라 분석 배치와 한 행에 합산된다.
            # 브리핑 몫만 보려면 여기서 봐야 한다
            "input_tokens": briefing.input_tokens,
            "output_tokens": briefing.output_tokens,
        },
    )

    print(body)
    return 0 if sent else 1


def _ratio(row: DailyKeyword) -> str:
    """배수 꼬리표. 기준선이 없으면 배수를 낼 수 없어 그렇게 적는다."""
    if row.surge_ratio is None:
        return " (처음)"
    return f" (평소 {row.ma7:.1f}회, {row.surge_ratio:.1f}배)"


def _sectors(raw: Any) -> list[tuple[str, str]]:
    """스키마가 형태를 강제하지만 값이 비어 오는 것은 막지 못한다."""
    return [
        (str(item["sector"]).strip(), str(item["note"]).strip())
        for item in (raw or [])
        if str(item.get("sector", "")).strip()
    ]


def _send(body: str, notifier: Notifier | None) -> bool:
    try:
        if notifier is None:
            notifier = TelegramNotifier.from_env()
    except RuntimeError:
        logger.exception("알림 설정이 없어 브리핑을 보내지 못합니다")
        return False
    return notifier.send("INFO", "정보수집 브리핑", body)


def _log(day: date, level: Level, title: str, detail: dict[str, Any]) -> None:
    """브리핑 본문은 DB 에 넣지 않는다. 돌았다는 사실만 남긴다."""
    with connect() as conn, transaction(conn) as cur:
        log_event(
            cur,
            PROCESS,
            level,
            title,
            category="collect",
            detail={"date": str(day), **detail},
        )


if __name__ == "__main__":
    sys.exit(run_with_heartbeat(PROCESS, main, sys.argv))
