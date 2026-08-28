# 공공 API 응답 파싱을 실제 응답 샘플로 고정해 확인한다

import itertools
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from collectors.market import public_indicators as mod
from collectors.market.public_indicators import (
    CustomsExportCollector,
    EcosCollector,
    KofiaCollector,
    month_start,
    to_decimal,
)

SINCE = datetime(2020, 1, 1, tzinfo=UTC)

# 2026-08-29 실제 응답에서 그대로 옮긴 행이다
DEPOSIT_ROWS = [
    {
        "basDt": "20260826",
        "invrDpsgAmt": "98917653413503",
        "brkTrdUcolMny": "1149188690131",
        "ucolMnyVsOppsTrdRlImpt": "1.3",
    },
    {
        "basDt": "20260825",
        "invrDpsgAmt": "102534149924256",
        "brkTrdUcolMny": "1002274079575",
        "ucolMnyVsOppsTrdRlImpt": ".9",
    },
]

CREDIT_ROWS = [
    {
        "basDt": "20260826",
        "crdTrFingWhl": "33102366526442",
        "crdTrFingScrs": "26246925916498",
        "crdTrFingKosdaq": "6855440609944",
    }
]

# 관세청은 마지막에 총계 행이 붙는다
CUSTOMS_ROWS = [
    {"expDlr": "101956159193", "impDlr": "66051585858", "year": "2026.06"},
    {"expDlr": "98959098818", "impDlr": "68567473420", "year": "2026.07"},
    {"expDlr": "200915258011", "impDlr": "134619059278", "year": "총계"},
]

# 품목별은 10자리 세부코드로 쪼개져 온다
ITEM_ROWS = [
    {"expDlr": "2286791909", "hsCode": "8542311000", "year": "2026.06"},
    {"expDlr": "11175623231", "hsCode": "8542321010", "year": "2026.06"},
    {"expDlr": "188327334", "hsCode": "8542313000", "year": "2026.06"},
    {"expDlr": "999", "hsCode": "8542311000", "year": "2026.07"},
    {"expDlr": "14650742474", "hsCode": "", "year": "총계"},
]

ECOS_ROWS = [
    {"TIME": "20260803", "DATA_VALUE": "1433.6", "UNIT_NAME": "원"},
    {"TIME": "20260804", "DATA_VALUE": "1429.9", "UNIT_NAME": "원"},
]


def test_빈_값은_0_이_아니라_none_이다():
    # 0 으로 만들면 '예탁금 0원' 이라는 값이 지표에 들어간다
    assert to_decimal("") is None
    assert to_decimal("   ") is None
    assert to_decimal("abc") is None
    assert to_decimal(" 1433.6 ") == Decimal("1433.6")


def test_총계_행은_월이_아니다():
    assert month_start("2026.06") == date(2026, 6, 1)
    assert month_start("총계") is None


def test_투자자예탁금_매핑(monkeypatch):
    monkeypatch.setattr(mod, "data_go_kr_json", lambda path, **kw: DEPOSIT_ROWS)

    result = KofiaCollector(
        "DEPOSIT", "getSecuritiesMarketTotalCapitalInfo", "invrDpsgAmt", 400
    ).collect(SINCE)

    assert result.success is True
    assert [r.period_date for r in result.records] == [
        date(2026, 8, 26),
        date(2026, 8, 25),
    ]
    assert result.records[0].value == Decimal(98917653413503) / Decimal(10**8)


def test_신용거래융자_매핑(monkeypatch):
    monkeypatch.setattr(mod, "data_go_kr_json", lambda path, **kw: CREDIT_ROWS)

    result = KofiaCollector(
        "CREDIT_BALANCE", "getGrantingOfCreditBalanceInfo", "crdTrFingWhl", 400
    ).collect(SINCE)

    assert result.records[0].value == Decimal(33102366526442) / Decimal(10**8)
    assert result.records[0].indicator_code == "CREDIT_BALANCE"


def test_since_이전_지표는_만들지_않는다(monkeypatch):
    monkeypatch.setattr(mod, "data_go_kr_json", lambda path, **kw: DEPOSIT_ROWS)

    result = KofiaCollector("DEPOSIT", "op", "invrDpsgAmt", 400).collect(
        datetime(2026, 8, 26, tzinfo=UTC)
    )

    assert [r.period_date for r in result.records] == [date(2026, 8, 26)]


def test_수출총액에서_총계_행을_뺀다(monkeypatch):
    monkeypatch.setattr(mod, "data_go_kr_xml", lambda path, **kw: CUSTOMS_ROWS)

    result = CustomsExportCollector("EXPORT_YOY", "202601", "202607").collect(SINCE)

    # 총계를 빼지 않으면 한 달치가 두 배 이상으로 들어간다
    assert [r.period_date for r in result.records] == [
        date(2026, 6, 1),
        date(2026, 7, 1),
    ]
    assert result.records[0].value == Decimal(101956159193)


def test_품목별_세부코드를_달별로_합산한다(monkeypatch):
    monkeypatch.setattr(mod, "data_go_kr_xml", lambda path, **kw: ITEM_ROWS)

    result = CustomsExportCollector(
        "EXPORT_SEMI_YOY", "202601", "202607", hs_code="8542"
    ).collect(SINCE)

    got = {r.period_date: r.value for r in result.records}
    assert got[date(2026, 6, 1)] == Decimal(2286791909 + 11175623231 + 188327334)
    assert got[date(2026, 7, 1)] == Decimal(999)


def test_품목별은_품목별_api_를_쓴다(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        mod, "data_go_kr_xml", lambda path, **kw: seen.update(path=path, **kw) or []
    )

    CustomsExportCollector("X", "202601", "202607", hs_code="8542").collect(SINCE)

    assert "Itemtrade" in seen["path"]
    assert seen["hsSgn"] == "8542"


def test_총괄은_총괄_api_를_쓴다(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        mod, "data_go_kr_xml", lambda path, **kw: seen.update(path=path) or []
    )

    CustomsExportCollector("X", "202601", "202607").collect(SINCE)

    assert "Newtrade" in seen["path"]


def test_환율_매핑(monkeypatch):
    monkeypatch.setattr(mod, "ecos_rows", lambda *a: ECOS_ROWS)

    result = EcosCollector(
        "USDKRW", "731Y001", "0000001", date(2026, 8, 29), 400
    ).collect(SINCE)

    assert [r.period_date for r in result.records] == [
        date(2026, 8, 3),
        date(2026, 8, 4),
    ]
    assert result.records[0].value == Decimal("1433.6")


@pytest.mark.parametrize(
    "collector, expected",
    [
        (KofiaCollector("DEPOSIT", "op", "f", 1), "kofia"),
        (CustomsExportCollector("EXPORT_YOY", "202601", "202602"), "customs"),
        (EcosCollector("USDKRW", "731Y001", "0000001", date(2026, 8, 29), 1), "ecos"),
    ],
)
def test_소스_종류를_밝힌다(collector, expected):
    assert collector.source_kind == expected


def test_금액은_억원으로_바꾼다(monkeypatch):
    # indicator 표가 단위를 억원으로 정의한다. 원으로 넣으면
    # 정의와 어긋나고 NUMERIC(20,6) 도 넘친다 (예탁금 100조)
    monkeypatch.setattr(mod, "data_go_kr_json", lambda path, **kw: DEPOSIT_ROWS)

    result = KofiaCollector("DEPOSIT", "op", "invrDpsgAmt", 400).collect(SINCE)

    assert result.records[0].value == Decimal(98917653413503) / Decimal(10**8)
    assert result.records[0].value < Decimal(10**14)


def test_구간을_일년_이하로_쪼갠다():
    from collectors.market.public_indicators import split_months

    # 관세청이 1년을 넘는 조회를 거부한다
    chunks = split_months("202401", "202608", 12)

    assert chunks == [("202401", "202412"), ("202501", "202512"), ("202601", "202608")]


def test_구간이_짧으면_한_번만_부른다():
    from collectors.market.public_indicators import split_months

    assert split_months("202601", "202607", 12) == [("202601", "202607")]


def test_쪼갠_구간이_겹치지_않는다():
    from collectors.market.public_indicators import split_months

    chunks = split_months("202401", "202612", 12)
    for (_, prev_end), (next_start, _) in itertools.pairwise(chunks):
        assert prev_end < next_start
