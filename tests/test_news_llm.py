# LLM 키워드 추출. 순서 어긋남과 비용 계산을 고정한다

from dataclasses import dataclass
from decimal import Decimal

import pytest

from collectors.news.llm import KeywordExtractor, clean_terms, cost_of

PRICES = {"price_input_per_mtok": 1.0, "price_output_per_mtok": 5.0}


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
        return FakeResponse([FakeBlock(self.payload)], FakeUsage(1000, 200))


class FakeClient:
    def __init__(self, payload: str):
        self.messages = FakeMessages(payload)


def _extractor(payload: str) -> KeywordExtractor:
    return KeywordExtractor(FakeClient(payload), "claude-haiku-4-5", 4000, 1200)


def test_extract_keeps_input_order():
    extractor = _extractor('{"results": [["HBM"], [], ["유리기판", "소부장"]]}')
    result = extractor.extract(["가", "나", "다"])

    assert result.keywords == [["HBM"], [], ["유리기판", "소부장"]]
    assert (result.input_tokens, result.output_tokens) == (1000, 200)


def test_count_mismatch_raises():
    """출력이 입력보다 적으면 어느 글의 키워드인지 알 수 없다. 배치를 버린다."""
    extractor = _extractor('{"results": [["HBM"]]}')
    with pytest.raises(ValueError):
        extractor.extract(["가", "나"])


def test_json_format_is_forced():
    """전문이나 코드펜스가 섞일 자리를 없앤다 (INTERFACES.md 7.1)."""
    extractor = _extractor('{"results": [[]]}')
    extractor.extract(["가"])
    assert extractor.client.messages.kwargs["output_config"]["format"]["type"] == (
        "json_schema"
    )


def test_long_text_is_cut():
    extractor = KeywordExtractor(FakeClient('{"results": [[]]}'), "m", 100, 10)
    extractor.extract(["가" * 50])
    sent = extractor.client.messages.kwargs["messages"][0]["content"]
    assert sent == "[1] " + "가" * 10


def test_clean_terms_drops_sentences_and_duplicates():
    assert clean_terms(["  HBM ", "HBM", "가" * 40, ""]) == ["HBM"]


def test_cost_uses_decimal():
    """금액에 float 를 쓰지 않는다 (CLAUDE.md 4)."""
    cost = cost_of(1_000_000, 1_000_000, PRICES)
    assert cost == Decimal("6.0")
    assert isinstance(cost, Decimal)


def test_workspace_header_only_when_set(monkeypatch):
    """개인 키는 워크스페이스가 없다. 빈 헤더를 보내면 안 된다."""
    from collectors.news import analyze

    monkeypatch.setattr(analyze, "load_env", lambda key: None)
    assert analyze.workspace_header() == {}

    monkeypatch.setattr(analyze, "load_env", lambda key: "wrkspc_1")
    assert analyze.workspace_header() == {"anthropic-workspace-id": "wrkspc_1"}
