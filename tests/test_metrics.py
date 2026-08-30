# 체결 짝짓기와 지표. 정의가 흔들리면 숫자의 뜻이 바뀐다

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from backtest.execution import Executor
from backtest.loop import Execution
from backtest.metrics import compute, pair_trades, survivorship_note

STOCK = "KRX:005930"
OTHER = "KRX:000660"
OPEN = time(9, 0)
DAYS = [date(2025, 3, 3) + timedelta(days=n) for n in range(4)]

PARAMS = {
    "fee_rate": 0.00015,
    "slippage_rate": 0.001,
    "tax_rate": {"KOSPI": 0.0020},
}
EXECUTOR = Executor(PARAMS)


def buy(stock, day, price, quantity, payload=None):
    return Execution(
        EXECUTOR.buy(stock, day, Decimal(price), quantity), "entry", payload
    )


def sell(stock, day, price, quantity, reason="timeout"):
    return Execution(
        EXECUTOR.sell(stock, day, Decimal(price), quantity, "KOSPI"), reason
    )


def curve(*values):
    return [(DAYS[i], Decimal(v)) for i, v in enumerate(values)]


# --- 짝짓기 -------------------------------------------------------------


def test_a_buy_and_a_sell_make_one_trade():
    trades = pair_trades(
        [buy(STOCK, DAYS[0], 10000, 10), sell(STOCK, DAYS[1], 11000, 10)], OPEN
    )

    assert len(trades) == 1
    trade = trades[0]
    assert trade.quantity == 10
    assert trade.exit_reason == "timeout"
    assert trade.pnl > 0
    assert trade.entry_at == datetime(2025, 3, 3, 0, 0, tzinfo=UTC)  # 09:00 KST


def test_fifo_sells_the_oldest_lot_first():
    """분할 매수는 먼저 산 것부터 판다 (2026-08-30 승인)."""
    trades = pair_trades(
        [
            buy(STOCK, DAYS[0], 10000, 10),
            buy(STOCK, DAYS[1], 20000, 10),
            sell(STOCK, DAYS[2], 20000, 10),
        ],
        OPEN,
    )

    closed = [t for t in trades if t.pnl is not None]
    assert len(closed) == 1
    # 판 것은 1만원짜리다. 2만원짜리를 팔았다면 이익이 나지 않는다
    assert closed[0].entry_price < Decimal(20000)
    assert closed[0].pnl > 0


def test_partial_sell_splits_the_lot():
    trades = pair_trades(
        [buy(STOCK, DAYS[0], 10000, 10), sell(STOCK, DAYS[1], 11000, 4)], OPEN
    )

    closed = [t for t in trades if t.pnl is not None]
    open_trades = [t for t in trades if t.pnl is None]
    assert closed[0].quantity == 4
    assert open_trades[0].quantity == 6


def test_unclosed_position_is_kept_with_empty_pnl():
    """미청산을 빼면 손익이 실제와 달라진다. 남기되 손익 칸은 비운다."""
    trades = pair_trades([buy(STOCK, DAYS[0], 10000, 10)], OPEN)

    assert len(trades) == 1
    assert trades[0].exit_at is None
    assert trades[0].pnl is None
    assert trades[0].exit_reason is None


def test_pnl_is_smaller_than_the_price_gap():
    """손익은 현금 증감이다. 단가 차이에서 거래비용만큼 줄어든다."""
    trades = pair_trades(
        [buy(STOCK, DAYS[0], 10000, 10), sell(STOCK, DAYS[1], 10000, 10)], OPEN
    )

    # 가격이 그대로인데 왕복 비용 때문에 손해다
    assert trades[0].pnl < 0
    assert trades[0].entry_price > trades[0].exit_price  # 슬리피지


def test_lots_are_kept_per_stock():
    trades = pair_trades(
        [
            buy(STOCK, DAYS[0], 10000, 10),
            buy(OTHER, DAYS[0], 10000, 10),
            sell(OTHER, DAYS[1], 11000, 10),
        ],
        OPEN,
    )

    closed = [t for t in trades if t.pnl is not None]
    assert len(closed) == 1
    assert closed[0].stock_id == OTHER


def test_entry_payload_survives_to_the_trade():
    trades = pair_trades([buy(STOCK, DAYS[0], 10000, 10, {"reason": "dummy"})], OPEN)
    assert trades[0].payload == {"reason": "dummy"}


# --- 지표 ---------------------------------------------------------------


def test_total_return_and_mdd():
    metrics = compute(
        Decimal(1000), curve(1000, 1200, 900, 1100), [], days_per_year=252
    )

    assert metrics.total_return == Decimal("0.1000")
    # 고점 1200 에서 900 까지. 300/1200 = 25%
    assert metrics.mdd == Decimal("0.2500")


def test_mdd_is_zero_when_equity_only_rises():
    metrics = compute(Decimal(1000), curve(1000, 1100, 1200), [], days_per_year=252)
    assert metrics.mdd == 0


def test_win_rate_counts_closed_trades_only():
    trades = pair_trades(
        [
            buy(STOCK, DAYS[0], 10000, 10),
            sell(STOCK, DAYS[1], 12000, 10),  # 이익
            buy(OTHER, DAYS[0], 10000, 10),
            sell(OTHER, DAYS[1], 9000, 10),  # 손실
            buy(STOCK, DAYS[2], 10000, 10),  # 미청산
        ],
        OPEN,
    )
    metrics = compute(Decimal(1000), curve(1000, 1000), trades, days_per_year=252)

    assert metrics.win_rate == Decimal("0.5000")
    assert metrics.trade_count == 3  # 미청산도 backtest_trade 에 남는다


def test_win_rate_is_none_without_a_closed_trade():
    """거래가 없는데 0% 를 적으면 '다 졌다' 로 읽힌다. 없는 것과 나쁜 것은 다르다."""
    metrics = compute(Decimal(1000), curve(1000, 1000), [], days_per_year=252)
    assert metrics.win_rate is None


def test_sharpe_needs_variation():
    flat = compute(Decimal(1000), curve(1000, 1000, 1000), [], days_per_year=252)
    assert flat.sharpe is None

    rising = compute(
        Decimal(1000), curve(1000, 1010, 1015, 1030), [], days_per_year=252
    )
    assert rising.sharpe > 0


def test_sharpe_is_none_with_too_few_days():
    metrics = compute(Decimal(1000), curve(1000, 1100), [], days_per_year=252)
    assert metrics.sharpe is None


def test_metrics_fit_the_column_scale():
    """backtest_run 의 지표 컬럼은 NUMERIC(10,4) 다."""
    metrics = compute(
        Decimal(1000), curve(1000, 1200, 900, 1100), [], days_per_year=252
    )

    for value in (metrics.total_return, metrics.mdd, metrics.sharpe):
        assert value is None or -value.as_tuple().exponent == 4


def test_survivorship_note_states_both_counts():
    note = survivorship_note(12, 3, date(2025, 1, 1))

    assert "12" in note and "3" in note
    assert "생존편향" in note
