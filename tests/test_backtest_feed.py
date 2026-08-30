# BacktestFeed. 커서 이후 데이터를 절대 주지 않는지 확인한다 (CLAUDE.md 필수 테스트)

from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest

from common.db import master, prices
from common.db.models import PriceDaily, Stock, StockStatus, make_stock_id
from common.feed.backtest import BacktestFeed
from common.types import Regime

CODE = "999980"
STOCK_ID = make_stock_id("KRX", CODE)
CLOSE_TIME = time(15, 30)

# 5일치. 커서를 가운데 두고 뒤쪽이 새지 않는지 본다.
# **실제 시세가 없는 구간을 쓴다.** 운영 데이터가 있는 날짜를 쓰면
# 유니버스 단정이 상위 종목들에 밀려 깨진다 (2026-08-30 에 깨졌다)
DAYS = [date(2019, 1, day) for day in (7, 8, 9, 10, 11)]


@pytest.fixture
def feed(cur):
    master.upsert_stocks(
        cur,
        [
            Stock(
                stock_id=STOCK_ID,
                exchange="KRX",
                code=CODE,
                board="KOSPI",
                name="피드테스트",
                listed_at=date(2018, 1, 2),
            )
        ],
    )
    prices.upsert_price_daily(
        cur,
        [
            PriceDaily(
                stock_id=STOCK_ID,
                trade_date=day,
                open=Decimal(100 + index),
                high=Decimal(110 + index),
                low=Decimal(90 + index),
                close=Decimal(100 + index),
                volume=1000,
                value=Decimal(100000),
            )
            for index, day in enumerate(DAYS)
        ],
    )
    return BacktestFeed(cur, DAYS[2], close_time=CLOSE_TIME, universe_size=10)


def test_candles_never_pass_the_cursor(feed):
    """커서 이후 봉이 하나라도 새면 백테스트가 미래를 본다."""
    candles = feed.get_candles(STOCK_ID, "1d", 100)

    assert [candle.close for candle in candles] == [
        Decimal(100),
        Decimal(101),
        Decimal(102),
    ]
    assert max(candle.ts for candle in candles) <= feed.now()


def test_cursor_moves_forward(feed):
    feed.set_date(DAYS[4])
    assert len(feed.get_candles(STOCK_ID, "1d", 100)) == 5


def test_now_is_market_close_in_utc(feed):
    """저장과 비교는 UTC 로 한다. 서버 시계가 어디에 있든 같아야 한다."""
    assert feed.now() == datetime(2019, 1, 9, 6, 30, tzinfo=UTC)


def test_candles_are_adjusted(cur, feed):
    """조정하지 않으면 분할일이 급락으로 보여 손절이 대량 발동한다."""
    # 5:1 분할. 이벤트 이전 가격의 조정계수가 1/5 이 된다
    prices.apply_adj_factor(cur, STOCK_ID, DAYS[1], Decimal(5))
    first = feed.get_candles(STOCK_ID, "1d", 100)[0]

    assert first.close == Decimal(20)
    assert first.volume == 5000


def test_quote_is_not_adjusted(cur, feed):
    """현재가는 조정 대상이 아니다 (INTERFACES.md 3.3)."""
    prices.apply_adj_factor(cur, STOCK_ID, DAYS[3], Decimal(5))
    assert feed.get_quote(STOCK_ID).price == Decimal(102)


def test_minute_interval_is_refused(feed):
    with pytest.raises(ValueError):
        feed.get_candles(STOCK_ID, "1m", 10)


def test_universe_uses_status_at_that_time(cur, feed):
    """오늘 관리종목인 회사를 2년 전 백테스트에서 빼면 미래 참조다."""
    master.open_stock_status(
        cur,
        [
            StockStatus(
                stock_id=STOCK_ID,
                valid_from=DAYS[3],
                board="KOSPI",
                is_managed=True,
            )
        ],
    )

    # 지정 이전 커서에서는 들어 있다
    assert STOCK_ID in feed.get_universe()

    # 지정 이후에는 빠진다
    feed.set_date(DAYS[4])
    assert STOCK_ID not in feed.get_universe()


def test_regime_defaults_to_neutral(cur):
    """국면 이력이 없는 과거 구간을 돌려도 판정을 지어내지 않는다."""
    feed = BacktestFeed(cur, date(2019, 1, 2), close_time=CLOSE_TIME, universe_size=10)
    assert feed.get_regime() == Regime.NEUTRAL


def test_signals_are_empty_until_engine_exists(feed):
    assert feed.get_signals("swing", feed.now()) == []
