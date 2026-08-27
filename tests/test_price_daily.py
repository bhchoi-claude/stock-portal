# KRX 일별매매정보를 PriceDaily 로 바꾸는 변환을 실제 응답 샘플로 고정해 확인한다

from datetime import date
from decimal import Decimal

from collectors.market.price_daily import collect, drop_unknown, to_price_daily

# 2026-08-26 기준 실제 응답에서 그대로 옮긴 행들이다. 값을 임의로 고치지 않는다
AJ_NETWORKS = {
    "BAS_DD": "20260826",
    "ISU_CD": "095570",
    "ISU_NM": "AJ네트웍스",
    "MKT_NM": "KOSPI",
    "SECT_TP_NM": "",
    "TDD_CLSPRC": "4490",
    "CMPPREVDD_PRC": "50",
    "FLUC_RT": "1.13",
    "TDD_OPNPRC": "4440",
    "TDD_HGPRC": "4495",
    "TDD_LWPRC": "4415",
    "ACC_TRDVOL": "60620",
    "ACC_TRDVAL": "270238001",
    "MKTCAP": "203184887910",
    "LIST_SHRS": "45252759",
}

# 거래량 0. 시·고·저가 0 으로 오고 종가만 값이 있다
DH_AUTONEXT = {
    "BAS_DD": "20260826",
    "ISU_CD": "000300",
    "ISU_NM": "DH오토넥스",
    "MKT_NM": "KOSPI",
    "SECT_TP_NM": "",
    "TDD_CLSPRC": "4200",
    "CMPPREVDD_PRC": "0",
    "FLUC_RT": "0.00",
    "TDD_OPNPRC": "0",
    "TDD_HGPRC": "0",
    "TDD_LWPRC": "0",
    "ACC_TRDVOL": "0",
    "ACC_TRDVAL": "0",
    "MKTCAP": "174035631000",
    "LIST_SHRS": "41437055",
}

# 종목코드에 영문자가 들어가는 우선주
CJ_PREF = {
    "BAS_DD": "20260826",
    "ISU_CD": "00104K",
    "ISU_NM": "CJ4우(전환)",
    "MKT_NM": "KOSPI",
    "SECT_TP_NM": "",
    "TDD_CLSPRC": "126100",
    "CMPPREVDD_PRC": "2600",
    "FLUC_RT": "2.11",
    "TDD_OPNPRC": "123900",
    "TDD_HGPRC": "126400",
    "TDD_LWPRC": "123000",
    "ACC_TRDVOL": "3532",
    "ACC_TRDVAL": "440586800",
    "MKTCAP": "532963163200",
    "LIST_SHRS": "4226512",
}

KOSDAQ_ROW = {
    "BAS_DD": "20260826",
    "ISU_CD": "060310",
    "ISU_NM": "3S",
    "MKT_NM": "KOSDAQ",
    "SECT_TP_NM": "벤처기업부",
    "TDD_CLSPRC": "1108",
    "CMPPREVDD_PRC": "24",
    "FLUC_RT": "2.21",
    "TDD_OPNPRC": "1110",
    "TDD_HGPRC": "1110",
    "TDD_LWPRC": "1050",
    "ACC_TRDVOL": "48242",
    "ACC_TRDVAL": "52746253",
    "MKTCAP": "58789416320",
    "LIST_SHRS": "53059040",
}


def test_전체_매핑():
    price = to_price_daily(AJ_NETWORKS)

    assert price.stock_id == "KRX:095570"
    assert price.trade_date == date(2026, 8, 26)
    assert price.open == Decimal(4440)
    assert price.high == Decimal(4495)
    assert price.low == Decimal(4415)
    assert price.close == Decimal(4490)
    assert price.volume == 60620
    assert price.value == Decimal(270238001)
    # 수집기는 조정계수를 계산하지 않는다
    assert price.adj_factor == Decimal(1)


def test_금액은_decimal_이다():
    price = to_price_daily(KOSDAQ_ROW)

    assert isinstance(price.close, Decimal)
    assert isinstance(price.value, Decimal)


def test_거래량_0_이면_시고저를_종가로_채운다():
    price = to_price_daily(DH_AUTONEXT)

    assert price.volume == 0
    assert price.open == price.high == price.low == price.close == Decimal(4200)


def test_거래량_0_이어도_거래_없음을_복원할_수_있다():
    # 도지로 만들되 volume 0 은 그대로 남긴다. 정보가 사라지면 안 된다
    assert to_price_daily(DH_AUTONEXT).volume == 0


def test_ohlc_불변식이_지켜진다():
    for row in (AJ_NETWORKS, DH_AUTONEXT, CJ_PREF, KOSDAQ_ROW):
        p = to_price_daily(row)

        assert p.low <= p.open <= p.high
        assert p.low <= p.close <= p.high


def test_종목코드에_영문자가_들어간다():
    # 우선주 82건이 여기에 해당한다. 숫자 6자리로 가정하면 안 된다
    assert to_price_daily(CJ_PREF).stock_id == "KRX:00104K"


def test_단축코드로_stock_id_를_만든다():
    # 이 API 의 ISU_CD 는 단축코드다. 종목기본정보의 표준코드와 다르다
    assert to_price_daily(KOSDAQ_ROW).stock_id == "KRX:060310"


def test_세_시장을_모두_모은다(monkeypatch):
    responses = {
        "stk_bydd_trd": [AJ_NETWORKS, CJ_PREF],
        "ksq_bydd_trd": [KOSDAQ_ROW],
        "knx_bydd_trd": [],
    }
    monkeypatch.setattr(
        "collectors.market.price_daily.fetch",
        lambda path, api_id, bas_dd: responses[api_id],
    )

    ids = [p.stock_id for p in collect("20260826")]

    assert ids == ["KRX:095570", "KRX:00104K", "KRX:060310"]


def test_stock_에_없는_종목은_건너뛴다():
    prices = [to_price_daily(r) for r in (AJ_NETWORKS, KOSDAQ_ROW)]

    kept, skipped = drop_unknown(prices, {"KRX:095570"})

    assert [p.stock_id for p in kept] == ["KRX:095570"]
    assert skipped == ["KRX:060310"]
