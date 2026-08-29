# DART 발행형태로 증가 이벤트를 분류하는 규칙을 확인한다

from datetime import date

from collectors.market.classify_increase import (
    classify_style,
    match_event,
    parse_dart_date,
    to_int,
)

EFFECTIVE = date(2025, 11, 24)

# 2026-08-29 실제 응답에서 옮긴 형태다
EVENTS = [
    {
        "isu_dcrs_de": "2025.11.06",
        "isu_dcrs_stle": "주식분할",
        "isu_dcrs_qy": "825,138",
    },
    {
        "isu_dcrs_de": "2025.08.20",
        "isu_dcrs_stle": "전환권행사",
        "isu_dcrs_qy": "150,894",
    },
    {
        "isu_dcrs_de": "2012.07.13",
        "isu_dcrs_stle": "유상증자(일반공모)",
        "isu_dcrs_qy": "1,948,811",
    },
]


def test_콤마가_있는_수를_읽는다():
    assert to_int("1,948,811") == 1948811
    assert to_int("825138") == 825138
    assert to_int("-") is None
    assert to_int("") is None


def test_dart_날짜를_읽는다():
    assert parse_dart_date("2025.11.06") == date(2025, 11, 6)
    assert parse_dart_date("-") is None


def test_대가_없이_늘면_조정_대상이다():
    assert classify_style("무상증자") == ("bonus", True)
    assert classify_style("주식분할") == ("split", True)


def test_대가를_받고_발행하면_조정_대상이_아니다():
    # 기존 주주의 주식 가치가 기계적으로 나뉘지 않는다
    assert classify_style("유상증자(일반공모)") == ("rights", False)
    assert classify_style("전환권행사") == ("rights", False)
    assert classify_style("신주인수권행사") == ("rights", False)
    assert classify_style("주식매수선택권행사") == ("rights", False)


def test_모르는_형태는_none_이다():
    # 추측해서 조정하면 시계열이 틀어진다
    assert classify_style("처음보는형태") is None


def test_총수로도_맞춘다():
    # DART 는 분할에서 증가분이 아니라 발행 후 총수를 적기도 한다
    # (2023-11-23 KRX:129890 은 총수 50,643,410 이었다)
    events = [
        {
            "isu_dcrs_de": "2023.11.09",
            "isu_dcrs_stle": "주식분할",
            "isu_dcrs_qy": "50,643,410",
        }
    ]

    found = match_event(events, {40514728, 50643410}, date(2023, 11, 23), 45)

    assert found["isu_dcrs_stle"] == "주식분할"


def test_우선주는_보통주_코드로_찾는다():
    from collectors.market.classify_increase import find_corp

    # 우선주는 DART 목록에 없다. 회사 단위로 등록되기 때문이다
    codes = {"001460": "00122579"}

    assert find_corp(codes, "KRX:001465") == "00122579"
    assert find_corp(codes, "KRX:001460") == "00122579"
    assert find_corp(codes, "KRX:999999") is None


def test_dart_에_없으면_비율의_모양으로_가른다():
    from collectors.market.classify_increase import fallback_by_ratio

    params = {"max_denominator": 4, "ratio_tolerance": 0.005}

    # 5:1 분할은 단순 분수라 조정 대상
    assert fallback_by_ratio(10128682, 50643410, params) == ("split", True)
    # 유상증자는 임의 비율이라 조정 대상이 아니다
    assert fallback_by_ratio(1000000, 1586200, params) == ("rights", False)


def test_수량으로_맞춘다():
    # DART 는 발행일(2025.11.06), 우리는 상장주식수 변경일(2025-11-24)이다.
    # 18일 어긋나므로 날짜로는 못 맞춘다
    found = match_event(EVENTS, {825138}, EFFECTIVE, window_days=45)

    assert found["isu_dcrs_stle"] == "주식분할"


def test_수량이_다르면_맞추지_않는다():
    assert match_event(EVENTS, {999999}, EFFECTIVE, window_days=45) is None


def test_창_밖이면_맞추지_않는다():
    # 수량이 같아도 13년 전 발행이면 다른 사건이다
    assert match_event(EVENTS, {1948811}, EFFECTIVE, window_days=45) is None


def test_수량이_같은_것이_여럿이면_가장_가까운_날을_고른다():
    events = [
        {
            "isu_dcrs_de": "2025.11.20",
            "isu_dcrs_stle": "무상증자",
            "isu_dcrs_qy": "100",
        },
        {
            "isu_dcrs_de": "2025.10.01",
            "isu_dcrs_stle": "유상증자",
            "isu_dcrs_qy": "100",
        },
    ]

    assert (
        match_event(events, {100}, EFFECTIVE, window_days=60)["isu_dcrs_stle"]
        == "무상증자"
    )
