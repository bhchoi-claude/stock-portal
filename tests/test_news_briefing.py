# 일일 브리핑. 표본 구성과 서식, 값이 비었을 때의 동작을 고정한다

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from collectors.news.briefing import (
    Briefing,
    Material,
    build_prompt,
    render,
    write,
)
from common.db.keywords import DailyKeyword

DAY = date(2026, 9, 4)

PARAMS = {
    "top_keywords": 12,
    "sample_keywords": 2,
    "sample_messages": 3,
    "sample_chars": 20,
    "max_output_tokens": 1500,
    "max_body_chars": 3800,
}


def _row(term: str, count: int, ma7, ratio) -> DailyKeyword:
    return DailyKeyword(
        keyword_id=abs(hash(term)) % 10000,
        term=term,
        mention_count=count,
        weighted_count=Decimal(count),
        ma7=None if ma7 is None else Decimal(str(ma7)),
        surge_ratio=None if ratio is None else Decimal(str(ratio)),
        is_confirmed=False,
    )


def _material(**over) -> Material:
    base = {
        "day": DAY,
        "ranked": [
            _row("유리기판", 12, "1.8", "6.7"),
            _row("폴더블", 9, None, None),
        ],
        "samples": [
            ("유리기판", ["유리기판 수주 소식이 이어진다"]),
            ("폴더블", ["폴더블 출하"]),
        ],
        "message_count": 314,
    }
    return Material(**{**base, **over})


def _briefing(**over) -> Briefing:
    base = {
        "strong": [("반도체 소재", "유리기판 관련 언급이 늘었다.")],
        "weak": [],
        "points": ["유리기판이 평소의 여섯 배로 언급됐다."],
        "input_tokens": 900,
        "output_tokens": 300,
    }
    return Briefing(**{**base, **over})


# --- 서식 ---


def test_render_lists_every_ranked_keyword():
    """LLM 이 표본으로 본 것은 상위 일부지만 목록은 전부 싣는다."""
    body = render(_material(), _briefing(), PARAMS["max_body_chars"])

    assert " 1. 유리기판 12회 (평소 1.8회, 6.7배)" in body
    assert " 2. 폴더블 9회 (처음)" in body


def test_render_omits_empty_sections():
    """약세가 없으면 빈 제목만 남기지 않는다. 매일 오는 알림이다."""
    body = render(_material(), _briefing(weak=[], points=[]), PARAMS["max_body_chars"])

    assert "[ 강세 ]" in body
    assert "[ 약세 ]" not in body
    assert "[ 볼 만한 것 ]" not in body


def test_render_caps_length():
    """텔레그램 상한을 넘기면 발송 자체가 실패한다. 잘라서라도 보낸다."""
    long_points = ["가" * 500 for _ in range(20)]
    body = render(_material(), _briefing(points=long_points), 200)

    assert len(body) == 200


def test_render_has_no_markdown():
    """평문으로 보낸다. 종목명의 기호가 서식과 부딪히면 400 이 난다."""
    body = render(_material(), _briefing(), PARAMS["max_body_chars"])

    assert "*" not in body
    assert "_" not in body


# --- 표본 ---


def test_prompt_carries_ratio_and_samples():
    """배수는 목록에 있고 논조는 원문에만 있다. 둘 다 넘겨야 한다."""
    prompt = build_prompt(_material(), PARAMS["sample_chars"])

    assert "## 유리기판 — 12회 (평소 1.8회, 6.7배)" in prompt
    assert "유리기판 수주 소식이 이어진다"[:20] in prompt


def test_prompt_truncates_each_sample():
    material = _material(samples=[("유리기판", ["가" * 500])])
    prompt = build_prompt(material, 20)

    assert "가" * 20 in prompt
    assert "가" * 21 not in prompt


# --- LLM 응답 ---


@dataclass
class FakeBlock:
    text: str
    type: str = "text"


@dataclass
class FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class FakeResponse:
    content: list
    usage: FakeUsage


class FakeMessages:
    def __init__(self, payload: str):
        self.payload = payload
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return FakeResponse([FakeBlock(self.payload)], FakeUsage(900, 300))


class FakeClient:
    def __init__(self, payload: str):
        self.messages = FakeMessages(payload)


def test_write_parses_sections():
    payload = json.dumps(
        {
            "strong": [{"sector": "반도체 소재", "note": "언급이 늘었다."}],
            "weak": [{"sector": "2차전지", "note": "단가 하락 이야기가 나왔다."}],
            "points": ["유리기판이 여섯 배로 언급됐다."],
        }
    )
    result = write(FakeClient(payload), _material(), PARAMS, "claude-haiku-4-5")

    assert result.strong == [("반도체 소재", "언급이 늘었다.")]
    assert result.weak == [("2차전지", "단가 하락 이야기가 나왔다.")]
    assert result.points == ["유리기판이 여섯 배로 언급됐다."]
    assert (result.input_tokens, result.output_tokens) == (900, 300)


def test_write_drops_blank_entries():
    """스키마가 형태는 강제하지만 빈 문자열은 막지 못한다."""
    payload = json.dumps(
        {
            "strong": [{"sector": "  ", "note": "무엇"}],
            "weak": [],
            "points": ["", "  ", "쓸 것."],
        }
    )
    result = write(FakeClient(payload), _material(), PARAMS, "claude-haiku-4-5")

    assert result.strong == []
    assert result.points == ["쓸 것."]


def test_write_uses_json_schema():
    """형식은 프롬프트가 아니라 스키마로 강제한다 (INTERFACES.md 7.1)."""
    client = FakeClient(json.dumps({"strong": [], "weak": [], "points": []}))
    write(client, _material(), PARAMS, "claude-haiku-4-5")

    assert client.messages.kwargs["output_config"]["format"]["type"] == "json_schema"
    assert client.messages.kwargs["max_tokens"] == 1500
