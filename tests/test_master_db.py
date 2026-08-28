# 실제 PostgreSQL 에 붙여 기준 데이터 upsert 와 조회를 확인한다. 모든 행은 롤백된다

from dataclasses import replace
from datetime import date
from decimal import Decimal

import psycopg
import pytest

from common.db import master
from common.db.conn import load_database_url
from common.db.events import log_event
from common.db.models import Account, Holiday, Source, Stock, StockStatus, make_stock_id

TEST_CODE = "999990"
TEST_STOCK_ID = make_stock_id("KRX", TEST_CODE)


@pytest.fixture
def cur():
    """트랜잭션을 열고 테스트가 끝나면 롤백한다. DB 에 흔적을 남기지 않는다."""
    try:
        url = load_database_url()
    except RuntimeError:
        pytest.skip("DATABASE_URL 이 없어 DB 통합 테스트를 건너뜁니다")

    conn = psycopg.connect(url)
    try:
        with conn.cursor() as c:
            yield c
    finally:
        conn.rollback()
        conn.close()


def make_test_indicator(cur, code: str = "TEST_IND") -> str:
    """테스트 전용 지표를 만든다. 롤백되므로 흔적이 남지 않는다.

    VKOSPI 같은 실제 코드를 쓰면 운영 데이터와 섞여 단정이 깨진다.
    2026-08-28 에 실제 지표가 들어온 뒤 실제로 깨졌다.
    """
    cur.execute(
        "INSERT INTO indicator (indicator_code, name, layer, frequency, source)"
        " VALUES (%s, %s, 'risk', 'daily', 'test')"
        " ON CONFLICT (indicator_code) DO NOTHING",
        (code, "테스트 지표"),
    )
    return code


def sample_stock(**overrides) -> Stock:
    base = {
        "stock_id": TEST_STOCK_ID,
        "exchange": "KRX",
        "code": TEST_CODE,
        "board": "KOSPI",
        "name": "테스트종목",
        "sector": "전기전자",
        "listed_shares": 1000,
        "listed_at": date(2020, 1, 2),
    }
    return Stock(**{**base, **overrides})


def test_get_stock_은_컬럼_순서대로_읽는다(cur):
    master.upsert_stocks(cur, [sample_stock()])
    got = master.get_stock(cur, TEST_STOCK_ID)

    assert got == sample_stock()


def test_없는_종목은_none(cur):
    assert master.get_stock(cur, "KRX:000000") is None


def test_재적재는_기존_값을_지우지_않는다(cur):
    master.upsert_stocks(cur, [sample_stock()])
    # sector·listed_shares·listed_at 이 없는 출처로 다시 적재한다
    master.upsert_stocks(
        cur,
        [
            sample_stock(
                name="테스트종목우", sector=None, listed_shares=None, listed_at=None
            )
        ],
    )

    got = master.get_stock(cur, TEST_STOCK_ID)
    assert got.name == "테스트종목우"
    assert got.sector == "전기전자"
    assert got.listed_shares == 1000
    assert got.listed_at == date(2020, 1, 2)


def test_상태는_열린_행이_있으면_새로_열지_않는다(cur):
    master.upsert_stocks(cur, [sample_stock()])
    status = StockStatus(
        stock_id=TEST_STOCK_ID, valid_from=date(2026, 8, 25), board="KOSPI"
    )
    master.open_stock_status(cur, [status])
    master.open_stock_status(cur, [replace(status, valid_from=date(2026, 8, 26))])

    cur.execute(
        "SELECT COUNT(*) FROM stock_status WHERE stock_id = %s AND valid_to IS NULL",
        (TEST_STOCK_ID,),
    )
    assert cur.fetchone()[0] == 1


def test_휴장일_upsert(cur):
    master.upsert_holidays(cur, [Holiday("KRX", date(2026, 1, 1), "신정")])
    master.upsert_holidays(cur, [Holiday("KRX", date(2026, 1, 1), "신정(수정)")])

    cur.execute(
        "SELECT name FROM exchange_holiday WHERE exchange='KRX' AND holiday_date=%s",
        (date(2026, 1, 1),),
    )
    assert cur.fetchone()[0] == "신정(수정)"


def test_계좌_upsert(cur):
    # 실계좌를 건드리지 않는다. 모의 계좌만 쓴다 (CLAUDE.md 실계좌 보호)
    account = Account(
        account_id="test_paper", broker="kiwoom", strategy="swing", is_paper=True
    )
    master.upsert_accounts(cur, [account])
    master.upsert_accounts(cur, [account])

    cur.execute("SELECT is_paper FROM account WHERE account_id='test_paper'")
    assert cur.fetchone()[0] is True


def test_소스_upsert(cur):
    src = Source(
        kind="dart", identifier="test-dart", name="테스트", weight=Decimal("1.5")
    )
    master.upsert_sources(cur, [src])
    master.upsert_sources(cur, [src])

    cur.execute("SELECT COUNT(*), MAX(weight) FROM source WHERE identifier='test-dart'")
    count, weight = cur.fetchone()
    assert (count, weight) == (1, Decimal("1.5"))


def test_빈_목록은_아무것도_하지_않는다(cur):
    assert master.upsert_stocks(cur, []) == 0
    assert master.upsert_holidays(cur, []) == 0
    assert master.open_stock_status(cur, []) == 0


def test_event_log_기록(cur):
    event_id = log_event(
        cur, "pytest", "INFO", "테스트 이벤트", category="system", detail={"k": 1}
    )
    cur.execute("SELECT detail FROM event_log WHERE event_id = %s", (event_id,))
    assert cur.fetchone()[0] == {"k": 1}


def test_폐지일을_마지막_거래일_다음날로_맞춘다(cur):
    stock = sample_stock(delisted_at=date(2024, 1, 1))
    master.upsert_stocks(cur, [stock])
    cur.executemany(
        "INSERT INTO price_daily (stock_id, trade_date, open, high, low, close, volume)"
        " VALUES (%s, %s, 100, 100, 100, 100, 1)",
        [(TEST_STOCK_ID, date(2023, 12, 20)), (TEST_STOCK_ID, date(2023, 12, 21))],
    )

    master.refine_delisted_at(cur)

    assert master.get_stock(cur, TEST_STOCK_ID).delisted_at == date(2023, 12, 22)


def test_상장중인_종목은_폐지일을_매기지_않는다(cur):
    master.upsert_stocks(cur, [sample_stock(delisted_at=None)])
    cur.execute(
        "INSERT INTO price_daily (stock_id, trade_date, open, high, low, close, volume)"
        " VALUES (%s, %s, 100, 100, 100, 100, 1)",
        (TEST_STOCK_ID, date(2023, 12, 20)),
    )

    master.refine_delisted_at(cur)

    assert master.get_stock(cur, TEST_STOCK_ID).delisted_at is None


def test_조정계수는_이벤트_이전_가격에만_붙는다(cur):
    from decimal import Decimal

    from common.db.prices import apply_adj_factor, reset_adj_factor

    master.upsert_stocks(cur, [sample_stock()])
    cur.executemany(
        "INSERT INTO price_daily (stock_id, trade_date, open, high, low, close, volume)"
        " VALUES (%s, %s, 100, 100, 100, 100, 1)",
        [
            (TEST_STOCK_ID, date(2024, 11, 8)),
            (TEST_STOCK_ID, date(2024, 11, 11)),
            (TEST_STOCK_ID, date(2024, 11, 12)),
        ],
    )
    reset_adj_factor(cur, [TEST_STOCK_ID])

    # 50:1 감자. 이전 가격에 50 을 곱해야 이어진다
    apply_adj_factor(cur, TEST_STOCK_ID, date(2024, 11, 11), Decimal("0.02"))

    cur.execute(
        "SELECT trade_date, adj_factor FROM price_daily WHERE stock_id = %s"
        " ORDER BY trade_date",
        (TEST_STOCK_ID,),
    )
    assert cur.fetchall() == [
        (date(2024, 11, 8), Decimal("50.0000000000")),
        (date(2024, 11, 11), Decimal("1.0000000000")),
        (date(2024, 11, 12), Decimal("1.0000000000")),
    ]


def test_이벤트가_둘이면_조정계수가_누적된다(cur):
    from decimal import Decimal

    from common.db.prices import apply_adj_factor, reset_adj_factor

    master.upsert_stocks(cur, [sample_stock()])
    cur.executemany(
        "INSERT INTO price_daily (stock_id, trade_date, open, high, low, close, volume)"
        " VALUES (%s, %s, 100, 100, 100, 100, 1)",
        [(TEST_STOCK_ID, date(2024, 1, 5)), (TEST_STOCK_ID, date(2024, 6, 5))],
    )
    reset_adj_factor(cur, [TEST_STOCK_ID])

    apply_adj_factor(cur, TEST_STOCK_ID, date(2024, 3, 1), Decimal("0.5"))
    apply_adj_factor(cur, TEST_STOCK_ID, date(2024, 9, 1), Decimal("0.2"))

    cur.execute(
        "SELECT adj_factor FROM price_daily WHERE stock_id = %s AND trade_date = %s",
        (TEST_STOCK_ID, date(2024, 1, 5)),
    )
    # 두 이벤트 모두 이후에 있으므로 2 x 5 = 10
    assert cur.fetchone()[0] == Decimal("10.0000000000")


def test_분봉_파티션을_만들고_라우팅한다(cur):
    from datetime import UTC, datetime
    from decimal import Decimal

    from common.db.prices import (
        create_minute_partition,
        existing_minute_partitions,
        upsert_price_minute,
    )
    from common.types import Candle

    # 파티션이 없는 먼 미래 달을 고른다
    month = datetime(2029, 3, 1, tzinfo=UTC)
    assert "price_minute_202903" not in existing_minute_partitions(cur)

    name = create_minute_partition(cur, month)
    assert name == "price_minute_202903"
    assert name in existing_minute_partitions(cur)

    master.upsert_stocks(cur, [sample_stock()])
    ts = datetime(2029, 3, 15, 1, 0, tzinfo=UTC)
    upsert_price_minute(
        cur,
        [
            Candle(
                stock_id=TEST_STOCK_ID,
                ts=ts,
                open=Decimal(100),
                high=Decimal(100),
                low=Decimal(100),
                close=Decimal(100),
                volume=1,
            )
        ],
    )

    cur.execute(
        "SELECT tableoid::regclass::text FROM price_minute"
        " WHERE stock_id = %s AND ts = %s",
        (TEST_STOCK_ID, ts),
    )
    assert cur.fetchone()[0] == name


def test_지표값을_넣고_변화율을_계산한다(cur):
    from decimal import Decimal

    from collectors.base import IndicatorRecord
    from common.db.indicators import recompute_change_rate, upsert_indicator_values

    code = make_test_indicator(cur)
    rows = [
        IndicatorRecord(code, date(2026, 8, 26), Decimal(10)),
        IndicatorRecord(code, date(2026, 8, 27), Decimal(12)),
        IndicatorRecord(code, date(2026, 8, 28), Decimal(9)),
    ]
    upsert_indicator_values(cur, rows)
    recompute_change_rate(cur, code)

    cur.execute(
        "SELECT period_date, value, change_rate FROM indicator_value"
        " WHERE indicator_code = %s ORDER BY period_date",
        (code,),
    )
    got = cur.fetchall()

    # 첫 행은 이전 값이 없어 NULL, 이후는 (현재-이전)/|이전|
    assert got[0][2] is None
    assert got[1][2] == Decimal("0.2000")
    assert got[2][2] == Decimal("-0.2500")


def test_지표값을_다시_넣으면_덮어쓴다(cur):
    from decimal import Decimal

    from collectors.base import IndicatorRecord
    from common.db.indicators import upsert_indicator_values

    code = make_test_indicator(cur)
    upsert_indicator_values(cur, [IndicatorRecord(code, date(2026, 8, 28), Decimal(9))])
    upsert_indicator_values(
        cur, [IndicatorRecord(code, date(2026, 8, 28), Decimal(11))]
    )

    cur.execute(
        "SELECT COUNT(*), MAX(value) FROM indicator_value"
        " WHERE indicator_code = %s AND period_date = %s",
        (code, date(2026, 8, 28)),
    )
    assert cur.fetchone() == (1, Decimal("11.000000"))


def test_판정에_쓰는_지표만_고른다(cur):
    from common.db.indicators import active_indicators

    cur.execute(
        "UPDATE indicator SET use_in_regime = FALSE WHERE indicator_code = 'VKOSPI'"
    )

    assert "VKOSPI" in active_indicators(cur)
    assert "VKOSPI" not in active_indicators(cur, regime_only=True)


def test_등록되지_않은_소스는_건드리지_않는다(cur):
    from common.db.indicators import touch_source

    assert touch_source(cur, "krx", "없는소스") == 0


def test_국면_판정을_기록하고_직전_국면을_읽는다(cur):
    from decimal import Decimal

    from common.db.regime import previous_regime, upsert_market_regime

    upsert_market_regime(
        cur, date(2026, 8, 27), "neutral", Decimal("0.1"), {}, {}, "v1"
    )
    upsert_market_regime(
        cur, date(2026, 8, 28), "danger", Decimal("-0.5"), {}, {}, "v1"
    )

    assert previous_regime(cur, date(2026, 8, 28)) == "neutral"
    assert previous_regime(cur, date(2026, 8, 27)) is None


def test_수동_override_는_덮어쓰지_않는다(cur):
    from decimal import Decimal

    from common.db.regime import is_override, upsert_market_regime

    day = date(2026, 8, 28)
    upsert_market_regime(cur, day, "neutral", Decimal("0.1"), {}, {}, "v1")
    cur.execute(
        "UPDATE market_regime SET is_override = TRUE, override_reason = '수동'"
        " WHERE trade_date = %s",
        (day,),
    )

    changed = upsert_market_regime(cur, day, "safe", Decimal("0.9"), {}, {}, "v1")

    assert changed == 0
    assert is_override(cur, day) is True
    cur.execute("SELECT regime FROM market_regime WHERE trade_date = %s", (day,))
    assert cur.fetchone()[0] == "neutral"


def test_스냅샷의_decimal_이_문자열로_남는다(cur):
    from decimal import Decimal

    from common.db.regime import upsert_market_regime

    day = date(2026, 8, 28)
    upsert_market_regime(
        cur,
        day,
        "danger",
        Decimal("-0.5"),
        {"risk": Decimal(-1)},
        {"VKOSPI": Decimal("30.5")},
        "v1",
    )

    cur.execute(
        "SELECT layer_scores, indicators FROM market_regime WHERE trade_date = %s",
        (day,),
    )
    layers, indicators = cur.fetchone()
    # float 로 바꾸면 값이 미세하게 달라진다. 스냅샷은 보이는 그대로 남긴다
    assert layers == {"risk": "-1"}
    assert indicators == {"VKOSPI": "30.5"}


def test_기준일_이전의_최신_지표값을_찾는다(cur):
    from decimal import Decimal

    from collectors.base import IndicatorRecord
    from common.db.indicators import recompute_change_rate, upsert_indicator_values
    from common.db.regime import value_as_of

    code = make_test_indicator(cur)
    upsert_indicator_values(
        cur,
        [
            IndicatorRecord(code, date(2026, 8, 20), Decimal(10)),
            IndicatorRecord(code, date(2026, 8, 25), Decimal(12)),
            IndicatorRecord(code, date(2026, 8, 30), Decimal(99)),
        ],
    )
    recompute_change_rate(cur, code)

    assert value_as_of(cur, code, "value", date(2026, 8, 28)) == (
        date(2026, 8, 25),
        Decimal("12.000000"),
    )
    # 첫 행은 change_rate 가 NULL 이라 건너뛴다
    assert value_as_of(cur, code, "change_rate", date(2026, 8, 21)) is None


def test_모르는_metric_은_거부한다(cur):
    import pytest

    from common.db.regime import value_as_of

    with pytest.raises(ValueError):
        value_as_of(cur, "TEST_IND", "value; DROP TABLE stock", date(2026, 8, 28))
