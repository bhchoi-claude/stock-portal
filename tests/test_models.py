# 기준 데이터 dataclass 의 식별자 규칙과 기본값을 확인한다

from datetime import date

from common.db.models import Source, Stock, StockStatus, make_stock_id


def test_stock_id_접두어는_거래소다():
    # KOSPI/KOSDAQ 는 board 이지 접두어가 아니다 (CLAUDE.md 절대규칙 6)
    assert make_stock_id("KRX", "005930") == "KRX:005930"
    assert make_stock_id("KRX", "263750") == "KRX:263750"


def test_stock_기본값():
    s = Stock(
        stock_id=make_stock_id("KRX", "005930"),
        exchange="KRX",
        code="005930",
        board="KOSPI",
        name="삼성전자",
    )
    assert s.code == "005930"
    assert s.is_managed is False
    assert s.is_suspended is False
    assert s.delisted_at is None


def test_stock_status_는_valid_to_가_비어야_현재값이다():
    st = StockStatus(stock_id="KRX:005930", valid_from=date(2026, 8, 25), board="KOSPI")
    assert st.valid_to is None


def test_source_weight_는_decimal_이다():
    # 금액은 아니지만 NUMERIC 컬럼이라 float 로 두면 왕복에서 값이 흔들린다
    assert not isinstance(
        Source(kind="dart", identifier="dart", name="DART").weight, float
    )
