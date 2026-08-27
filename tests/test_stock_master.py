# KRX 응답을 Stock 으로 바꾸는 변환을 실제 응답 샘플로 고정해 확인한다

from datetime import date

import pytest

from collectors.market.stock_master import EQUITY_TYPES, collect, to_stock

# 2026-08-26 기준 실제 응답에서 그대로 옮긴 행들이다. 값을 임의로 고치지 않는다
SAMSUNG = {
    "ISU_CD": "KR7005930003",
    "ISU_SRT_CD": "005930",
    "ISU_NM": "삼성전자보통주",
    "ISU_ABBRV": "삼성전자",
    "ISU_ENG_NM": "SamsungElectronics",
    "LIST_DD": "19750611",
    "MKT_TP_NM": "KOSPI",
    "SECUGRP_NM": "주권",
    "SECT_TP_NM": "",
    "KIND_STKCERT_TP_NM": "보통주",
    "PARVAL": "100",
    "LIST_SHRS": "5846278608",
}

SAMSUNG_PREF = {
    "ISU_CD": "KR7005931001",
    "ISU_SRT_CD": "005935",
    "ISU_NM": "삼성전자1우선주",
    "ISU_ABBRV": "삼성전자우",
    "ISU_ENG_NM": "SamsungElectronics(1P)",
    "LIST_DD": "19890925",
    "MKT_TP_NM": "KOSPI",
    "SECUGRP_NM": "주권",
    "SECT_TP_NM": "",
    "KIND_STKCERT_TP_NM": "구형우선주",
    "PARVAL": "100",
    "LIST_SHRS": "802371203",
}

KOSDAQ_ROW = {
    "ISU_CD": "KR7098120009",
    "ISU_SRT_CD": "098120",
    "ISU_NM": "(주)마이크로컨텍솔루션",
    "ISU_ABBRV": "마이크로컨텍솔",
    "ISU_ENG_NM": "Micro Contact Solution Co.,Ltd.",
    "LIST_DD": "20080923",
    "MKT_TP_NM": "KOSDAQ",
    "SECUGRP_NM": "주권",
    "SECT_TP_NM": "우량기업부",
    "KIND_STKCERT_TP_NM": "보통주",
    "PARVAL": "500",
    "LIST_SHRS": "8312766",
}

KONEX_ROW = {
    "ISU_CD": "KR7260870001",
    "ISU_SRT_CD": "260870",
    "ISU_NM": "SK시그넷",
    "ISU_ABBRV": "SK시그넷",
    "ISU_ENG_NM": "SK Signet",
    "LIST_DD": "20170830",
    "MKT_TP_NM": "KONEX",
    "SECUGRP_NM": "주권",
    "SECT_TP_NM": "일반기업부",
    "KIND_STKCERT_TP_NM": "보통주",
    "PARVAL": "500",
    "LIST_SHRS": "22780385",
}


def variant(base: dict, **changes) -> dict:
    """실제 행을 변형해 만든 케이스. 관측값이 아니라는 뜻이다."""
    return {**base, **changes}


def test_삼성전자_전체_매핑():
    stock = to_stock(SAMSUNG)

    assert stock.stock_id == "KRX:005930"
    assert stock.exchange == "KRX"
    assert stock.code == "005930"
    assert stock.board == "KOSPI"
    assert stock.name == "삼성전자"
    assert stock.listed_at == date(1975, 6, 11)
    assert stock.listed_shares == 5846278608
    assert stock.is_preferred is False
    assert stock.is_spac is False
    assert stock.sector is None


def test_이름은_약명을_쓴다():
    # ISU_NM 은 '삼성전자보통주' 라서 화면에 쓸 이름이 아니다
    assert to_stock(SAMSUNG).name == "삼성전자"
    assert to_stock(SAMSUNG_PREF).name == "삼성전자우"


@pytest.mark.parametrize(
    "kind, expected",
    [("보통주", False), ("구형우선주", True), ("신형우선주", True), ("종류주권", True)],
)
def test_보통주가_아니면_우선주로_본다(kind, expected):
    row = variant(SAMSUNG, KIND_STKCERT_TP_NM=kind)

    assert to_stock(row).is_preferred is expected


def test_코스닥과_코넥스의_board():
    assert to_stock(KOSDAQ_ROW).board == "KOSDAQ"
    assert to_stock(KONEX_ROW).board == "KONEX"


def test_소속부는_업종이_아니라서_버린다():
    # SECT_TP_NM 이 '우량기업부' 라도 sector 로 들어가면 안 된다
    assert to_stock(KOSDAQ_ROW).sector is None


def test_스팩은_종목명으로_판정한다():
    row = variant(SAMSUNG, ISU_ABBRV="엔에이치스팩29호", ISU_SRT_CD="123456")

    assert to_stock(row).is_spac is True


def test_상장주식수가_비면_none():
    assert to_stock(variant(SAMSUNG, LIST_SHRS="")).listed_shares is None


def test_상장일이_비면_none():
    assert to_stock(variant(SAMSUNG, LIST_DD="")).listed_at is None


def test_주권_계열만_적재한다(monkeypatch):
    rows = [
        SAMSUNG,
        variant(SAMSUNG, ISU_SRT_CD="330590", SECUGRP_NM="부동산투자회사"),
        variant(SAMSUNG, ISU_SRT_CD="900140", SECUGRP_NM="외국주권"),
        variant(SAMSUNG, ISU_SRT_CD="088980", SECUGRP_NM="투자회사"),
        variant(SAMSUNG, ISU_SRT_CD="099340", SECUGRP_NM="사회간접자본투융자회사"),
    ]
    monkeypatch.setattr(
        "collectors.market.stock_master.fetch",
        lambda path, api_id, bas_dd: rows if api_id == "stk_isu_base_info" else [],
    )

    codes = [s.code for s in collect("20260826")]

    assert codes == ["005930", "900140"]


def test_주식예탁증권도_주권_계열이다():
    assert "주식예탁증권" in EQUITY_TYPES
    assert "부동산투자회사" not in EQUITY_TYPES
