# LLM 키워드 추출. 순서 어긋남과 비용 계산을 고정한다

from dataclasses import dataclass
from decimal import Decimal

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


def test_extract_maps_by_index():
    extractor = _extractor(
        '{"results": [{"index": 1, "keywords": ["HBM"]},'
        ' {"index": 3, "keywords": ["유리기판", "소부장"]}]}'
    )
    result = extractor.extract(["가", "나", "다"])

    assert result.keywords == {1: ["HBM"], 3: ["유리기판", "소부장"]}
    assert (result.input_tokens, result.output_tokens) == (1000, 200)


def test_missing_entries_are_left_out():
    """모델이 글 하나를 빼먹어도 나머지는 쓴다.

    번호가 없던 때는 20건 중 19건이 멀쩡해도 배치를 통째로 버렸다 (2026-08-30).
    """
    extractor = _extractor('{"results": [{"index": 2, "keywords": []}]}')
    result = extractor.extract(["가", "나"])
    assert result.keywords == {2: []}


def test_out_of_range_index_is_dropped():
    extractor = _extractor(
        '{"results": [{"index": 9, "keywords": ["HBM"]},'
        ' {"index": 0, "keywords": ["X"]}]}'
    )
    assert extractor.extract(["가", "나"]).keywords == {}


def test_schema_pins_the_count():
    extractor = _extractor('{"results": []}')
    extractor.extract(["가", "나", "다"])
    schema = extractor.client.messages.kwargs["output_config"]["format"]["schema"]
    assert schema["properties"]["results"]["minItems"] == 3
    assert schema["properties"]["results"]["maxItems"] == 3


def test_json_format_is_forced():
    """전문이나 코드펜스가 섞일 자리를 없앤다 (INTERFACES.md 7.1)."""
    extractor = _extractor('{"results": []}')
    extractor.extract(["가"])
    assert extractor.client.messages.kwargs["output_config"]["format"]["type"] == (
        "json_schema"
    )


def test_long_text_is_cut():
    extractor = KeywordExtractor(FakeClient('{"results": []}'), "m", 100, 10)
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
