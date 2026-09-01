# 규칙 분석. 표본에서 드러난 함정 셋을 고정한다 (2026-08-30)

import pytest

from collectors.news.analyzer import NewsAnalyzer
from common.db.messages import StoredMessage

FOOTER = "📌그로쓰리서치 실시간 특징주 받기"


@pytest.fixture
def analyzer():
    return NewsAnalyzer(
        stocks={"삼성전자": "KRX:005930", "SK하이닉스": "KRX:000660"},
        codes={"005930": "KRX:005930", "000660": "KRX:000660"},
        keywords={"반도체": 1, "유리기판": 2, "글라스기판": 2},
        min_length=4,
        footers=[FOOTER],
        ad_patterns=["리딩방"],
    )


def test_link_only_message_is_noise(analyzer):
    """가치투자클럽은 링크만 던진다. 25자지만 본문은 0자다."""
    assert analyzer.is_noise("https://naver.me/5xget14F")


def test_short_theme_post_survives(analyzer):
    """'원전 2Q' 는 7자짜리 테마 글이다. 세게 자르면 이런 것부터 사라진다."""
    assert not analyzer.is_noise("원전 2Q")
    assert not analyzer.is_noise("변압기 2Q")


def test_footer_is_stripped(analyzer):
    """꼬리표를 놔두면 채널 이름이 매일 급등 키워드가 된다."""
    text = analyzer.normalize(f"반도체 급등\n\n{FOOTER}\nhttps://t.me/rocket_news1")
    assert text == "반도체 급등"


def test_article_id_is_not_a_stock_code(analyzer):
    """링크 안의 기사 ID 는 여섯 자리를 품는다. 링크를 먼저 지운다."""
    raw = "제주 실종 https://n.news.naver.com/mnews/article/011/0004656438"
    assert analyzer.match_stocks(analyzer.normalize(raw)) == []


def test_stock_code_matches_after_normalize(analyzer):
    assert analyzer.match_stocks("특징주 005930 강세") == ["KRX:005930"]


def test_stock_name_matches(analyzer):
    found = analyzer.match_stocks("삼성전자·SK하이닉스 동반 하락")
    assert found == ["KRX:000660", "KRX:005930"]


def test_synonyms_map_to_one_keyword(analyzer):
    """'글라스기판' 과 '유리기판' 이 따로 세어지면 둘 다 급등으로 안 보인다."""
    found, _ = analyzer.match_keywords("글라스기판 수주")
    assert found == [2]


def test_matched_terms_leave_the_rest(analyzer):
    found, rest = analyzer.match_keywords("반도체 소부장 수주 확대")
    assert found == [1]
    assert "반도체" not in rest
    assert "소부장" in rest


def test_ad_pattern_is_filtered(analyzer):
    kept = analyzer.filter(
        [StoredMessage(1, "리딩방 무료 입장"), StoredMessage(2, "반도체 수주 확대")]
    )
    assert [m.message_id for m in kept] == [2]


def test_shorter_name_inside_longer_word_is_not_a_stock():
    """'이닉스' 는 실재하는 종목이지만 'SK하이닉스' 안의 것은 아니다.

    제외 목록으로 막으면 진짜 언급까지 잃는다. 규칙으로 가른다.
    """
    analyzer = _with({"이닉스": "KRX:226360", "SK하이닉스": "KRX:000660"})
    assert analyzer.match_stocks("SK하이닉스 강세") == ["KRX:000660"]
    assert analyzer.match_stocks("이닉스 신고가") == ["KRX:226360"]


def test_common_word_swallowing_a_name(analyzer):
    """'펩타이드' 안의 '타이드' 를 종목으로 세지 않는다."""
    analyzer.stocks["타이드"] = "KRX:999999"
    analyzer.stock_names = sorted(analyzer.stocks, key=len, reverse=True)
    assert analyzer.match_stocks("펩타이드 임상 결과") == []


def test_prefix_name_inside_longer_name():
    """'HD현대일렉트릭' 만 있는 글에서 'HD현대' 를 함께 세지 않는다."""
    analyzer = _with({"HD현대": "KRX:267250", "HD현대일렉트릭": "KRX:267260"})
    assert analyzer.match_stocks("HD현대일렉트릭 수주") == ["KRX:267260"]
    # 둘 다 실제로 나오면 둘 다 잡는다
    assert analyzer.match_stocks("HD현대일렉트릭과 HD현대 동반 상승") == [
        "KRX:267250",
        "KRX:267260",
    ]


def test_particle_after_name_still_matches():
    """'삼성전자가' 의 조사는 경계로 보지 않는다. 뒤쪽은 검사할 수 없다."""
    analyzer = _with({"삼성전자": "KRX:005930"})
    assert analyzer.match_stocks("삼성전자가 반등했다") == ["KRX:005930"]


def _with(stocks: dict[str, str]) -> NewsAnalyzer:
    return NewsAnalyzer(
        stocks=stocks,
        codes={},
        keywords={},
        min_length=4,
        footers=[],
        ad_patterns=[],
    )


# --- 키워드 경계와 길이 (2026-09-01 실측에서 나온 것) -------------------------


@pytest.fixture
def gold():
    """한 글자 키워드가 사전에 들어 있는 상태. 실제로 '금' 이 그랬다."""
    return NewsAnalyzer(
        stocks={},
        codes={},
        keywords={"금": 1, "AI": 2, "반도체": 3, "HBM": 4, "HBM3E": 5},
        min_length=4,
        footers=[],
        ad_patterns=[],
        min_term_length=2,
    )


def test_한_글자_키워드는_사전에서_걸러진다(gold):
    """'금' 이 신주발행금지·금리·현금에 걸려 하루 26회로 상위에 올랐다.

    낱말 경계 검사로는 못 막는다. '금리' 는 '금' 이 맨 앞이라 왼쪽 경계가
    깨끗하고, 오른쪽은 조사와 구분이 안 돼 볼 수 없다.
    """
    for text in ("본느, 신주발행금지 가처분", "금리 인하 기대", "현금 흐름", "금 가격"):
        assert gold.match_keywords(text)[0] == [], text


def test_낱말_안에_든_키워드는_안_걸린다():
    """종목명에는 처음부터 있던 검사다. 키워드에는 없었다."""
    analyzer = NewsAnalyzer(
        stocks={},
        codes={},
        keywords={"에너지": 1},
        min_length=4,
        footers=[],
        ad_patterns=[],
        min_term_length=2,
    )
    # '신재생에너지' 안의 '에너지' 는 앞에 글자가 붙어 있다
    assert analyzer.match_keywords("신재생에너지 확대")[0] == []
    assert analyzer.match_keywords("에너지 확대")[0] == [1]


def test_긴_표현을_먼저_지운다(gold):
    """'HBM3E' 를 먼저 지워야 'HBM' 이 남은 조각에 또 걸리지 않는다."""
    found, rest = gold.match_keywords("HBM3E 양산")

    assert found == [5]
    assert "HBM" not in rest


def test_지울_때도_경계를_지킨다(gold):
    """경계를 무시하고 지우면 LLM 이 망가진 문장을 본다."""
    _, rest = gold.match_keywords("반도체 업황과 신주발행금지 가처분")

    # '반도체' 는 지워지고 '신주발행금지' 는 온전히 남는다
    assert "반도체" not in rest
    assert "신주발행금지" in rest


def test_조사가_붙어도_걸린다(gold):
    """오른쪽은 보지 않는다. '반도체가' 의 조사와 구분이 안 되기 때문이다."""
    assert gold.match_keywords("반도체가 올랐다")[0] == [3]
