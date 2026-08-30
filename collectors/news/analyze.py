# 적재된 원문을 분석하는 배치. 사전으로 먼저 거르고 남은 것만 LLM 에 보낸다

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import anthropic

from common.config import load_config
from common.db import master
from common.db.conn import connect, transaction
from common.db.events import log_event
from common.db.heartbeat import run_with_heartbeat
from common.db.keywords import insert_keyword_mentions, keyword_terms, upsert_keywords
from common.db.messages import insert_stock_mentions, mark_analyzed, unanalyzed
from common.db.usage import record_usage, today_cost
from common.env import load_env, require_env
from common.notify.telegram import TelegramNotifier

from .analyzer import NewsAnalyzer
from .llm import KeywordExtractor, cost_of

logger = logging.getLogger(__name__)

PROCESS = "news_analyze"
PROVIDER = "anthropic"

# 사용량은 날짜별로 센다. 서버 시계가 어디에 있든 KST 기준이다
SEOUL = ZoneInfo("Asia/Seoul")


def build_analyzer(cur, params: dict[str, Any]) -> NewsAnalyzer:
    entries = master.stock_dictionary(
        cur, params["stock"]["min_name_length"], params["stock"]["excludes"]
    )
    return NewsAnalyzer(
        stocks={name: stock_id for name, stock_id, _ in entries},
        codes={code: stock_id for _, stock_id, code in entries},
        keywords=keyword_terms(cur),
        min_length=params["min_length"],
        footers=params["footers"],
        ad_patterns=params["ad_patterns"],
    )


def run_llm(leftovers: Sequence[tuple[int, str]], limits: dict[str, Any]) -> int:
    """미매칭 텍스트를 배치로 보내 키워드를 뽑는다. 처리한 글 수를 돌려준다.

    실패한 배치는 표시하지 않는다. `analyzed_at` 이 비어 있으면 다음 주기가
    다시 가져간다 (INTERFACES.md 7.1).
    """
    if not leftovers:
        return 0

    try:
        api_key = require_env("ANTHROPIC_API_KEY")
    except RuntimeError:
        logger.warning("ANTHROPIC_API_KEY 가 없어 LLM 단계를 건너뜁니다")
        return 0

    extractor = KeywordExtractor(
        anthropic.Anthropic(api_key=api_key, default_headers=workspace_header()),
        limits["model"],
        limits["max_output_tokens"],
        limits["max_chars"],
    )
    today = datetime.now(SEOUL).date()
    cap = Decimal(str(limits["daily_cost_usd"]))

    processed = 0
    for batch in _chunks(leftovers, limits["batch_size"]):
        # **호출 전에** 확인한다. 호출한 뒤에 세면 이미 쓴 돈이다
        with connect() as conn, transaction(conn) as cur:
            spent = today_cost(cur, PROVIDER, today)
        if spent >= cap:
            _stop_over_budget(spent, cap)
            break

        try:
            result = extractor.extract([text for _, text in batch])
        except Exception:
            # 이 배치만 건너뛴다. 다음 주기에 다시 가져간다
            logger.exception("LLM 배치 실패")
            continue

        cost = cost_of(result.input_tokens, result.output_tokens, limits)
        terms = sorted({t for terms in result.keywords.values() for t in terms})

        with connect() as conn, transaction(conn) as cur:
            record_usage(
                cur,
                today,
                PROVIDER,
                limits["model"],
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cost_usd=cost,
            )
            ids = upsert_keywords(cur, terms)
            insert_keyword_mentions(
                cur,
                [
                    (batch[index - 1][0], ids[term])
                    for index, extracted in result.keywords.items()
                    for term in extracted
                    if term in ids
                ],
            )
            answered = [batch[index - 1][0] for index in result.keywords]
            mark_analyzed(cur, answered, "llm")

        processed += len(answered)
        missing = len(batch) - len(answered)
        if missing:
            # 빠진 글은 표시하지 않았다. 다음 주기가 다시 가져간다
            logger.warning("배치 %d건 중 %d건 무응답", len(batch), missing)
        logger.info("배치 %d건, 키워드 %d종, $%s", len(answered), len(terms), cost)

    return processed


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    params = load_config("collect")["news"]
    limits = load_config("limits")["llm"]

    with connect() as conn, transaction(conn) as cur:
        analyzer = build_analyzer(cur, params)
        messages = unanalyzed(cur, params["batch_size"])

    if not messages:
        print("분석할 원문이 없습니다.")
        return 0

    kept = analyzer.filter(messages)
    kept_ids = {message.message_id for message in kept}
    noise = [m.message_id for m in messages if m.message_id not in kept_ids]

    stock_pairs: list[tuple[int, str]] = []
    keyword_pairs: list[tuple[int, int]] = []
    leftovers: list[tuple[int, str]] = []
    done: list[int] = []

    for message in kept:
        text = analyzer.normalize(message.content)
        stock_pairs += [(message.message_id, s) for s in analyzer.match_stocks(text)]
        matched, rest = analyzer.match_keywords(text)
        keyword_pairs += [(message.message_id, k) for k in matched]
        if rest:
            leftovers.append((message.message_id, rest))
        else:
            done.append(message.message_id)

    with connect() as conn, transaction(conn) as cur:
        insert_stock_mentions(cur, stock_pairs)
        insert_keyword_mentions(cur, keyword_pairs)
        mark_analyzed(cur, noise, "dict", filtered=True)
        # 사전만으로 끝난 글이다. 남은 표현이 없어 LLM 이 볼 것이 없다
        mark_analyzed(cur, done, "dict")

    analyzed = run_llm(leftovers, limits)

    with connect() as conn, transaction(conn) as cur:
        log_event(
            cur,
            PROCESS,
            "INFO",
            "원문 분석",
            category="collect",
            detail={
                "messages": len(messages),
                "filtered": len(noise),
                "stocks": len(stock_pairs),
                "keywords": len(keyword_pairs),
                "llm": analyzed,
                "pending": len(leftovers) - analyzed,
            },
        )

    print(
        f"원문 {len(messages)}건 중 {len(noise)}건 제외."
        f" 종목 {len(stock_pairs)}건, 사전 키워드 {len(keyword_pairs)}건."
        f" LLM {analyzed}건 처리, {len(leftovers) - analyzed}건 남음."
    )
    return 0


def workspace_header() -> dict[str, str]:
    """조직 계정의 키는 어느 워크스페이스에서 쓰는지 함께 보내야 한다.

    없으면 400 `anthropic-workspace-id is required` 가 난다 (2026-08-30 실측).
    개인 키는 이 값이 없어도 되므로 있을 때만 붙인다.
    """
    workspace = load_env("ANTHROPIC_WORKSPACE_ID")
    return {"anthropic-workspace-id": workspace} if workspace else {}


def _chunks(
    items: Sequence[tuple[int, str]], size: int
) -> list[Sequence[tuple[int, str]]]:
    return [items[start : start + size] for start in range(0, len(items), size)]


def _stop_over_budget(spent: Decimal, cap: Decimal) -> None:
    """상한에 닿으면 호출을 멈추고 알린다 (PROJECT.md 8.7, 10장)."""
    logger.warning("LLM 일일 상한 도달. $%s / $%s", spent, cap)
    with connect() as conn, transaction(conn) as cur:
        log_event(
            cur,
            PROCESS,
            "WARN",
            "LLM 일일 상한 도달",
            category="collect",
            detail={"spent_usd": str(spent), "cap_usd": str(cap)},
        )
    notifier = _notifier()
    if notifier is not None:
        notifier.send(
            "WARN",
            "LLM 일일 상한",
            f"오늘 ${spent} 를 써서 호출을 멈춥니다.\n남은 원문은 내일 이어서 분석합니다.",
        )


def _notifier() -> TelegramNotifier | None:
    try:
        return TelegramNotifier.from_env()
    except RuntimeError:
        logger.exception("알림 설정이 없어 상한 도달을 알리지 못합니다")
        return None


if __name__ == "__main__":
    sys.exit(run_with_heartbeat(PROCESS, main, sys.argv))
