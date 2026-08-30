# backtest_run·backtest_trade 적재. DB 통합 테스트라 롤백된다

from datetime import UTC, date, datetime
from decimal import Decimal

from backtest.metrics import Trade
from common.db.backtest import delisted_between, insert_run, insert_trades

FROM = date(2025, 3, 4)
TO = date(2025, 3, 31)
STOCK = "KRX:005930"

RUN = {
    "strategy": "dummy",
    "from_date": FROM,
    "to_date": TO,
    "universe": "거래대금 상위 200종목",
    "params": {"backtest": {"fee_rate": 0.00015}},
    "initial_capital": Decimal(10_000_000),
    "final_capital": Decimal(9_925_857),
    "total_return": Decimal("-0.0074"),
    "mdd": Decimal("0.0120"),
    "win_rate": Decimal("0.4800"),
    "trade_count": 61,
    "sharpe": Decimal("-1.2300"),
    "fee_rate": Decimal("0.00015"),
    "slippage_rate": Decimal("0.001"),
    "note": "생존편향 경고: ...",
}


def test_insert_run_returns_an_id(cur):
    run_id = insert_run(cur, **RUN)

    cur.execute(
        "SELECT strategy, total_return, note FROM backtest_run WHERE run_id = %s",
        (run_id,),
    )
    strategy, total_return, note = cur.fetchone()
    assert strategy == "dummy"
    assert total_return == Decimal("-0.0074")
    assert note.startswith("생존편향")


def test_zero_cost_run_records_that_it_was_zero(cur):
    """0 으로 돌린 결과도 그 사실이 남는다 (SCHEMA.md 7장)."""
    run_id = insert_run(
        cur, **{**RUN, "fee_rate": Decimal(0), "slippage_rate": Decimal(0)}
    )

    cur.execute(
        "SELECT fee_rate, slippage_rate FROM backtest_run WHERE run_id = %s", (run_id,)
    )
    assert cur.fetchone() == (Decimal(0), Decimal(0))


def test_metrics_may_be_null(cur):
    """계산할 수 없는 지표는 비운다. 0 으로 적으면 '다 졌다' 로 읽힌다."""
    run_id = insert_run(cur, **{**RUN, "win_rate": None, "sharpe": None})

    cur.execute(
        "SELECT win_rate, sharpe FROM backtest_run WHERE run_id = %s", (run_id,)
    )
    assert cur.fetchone() == (None, None)


def test_insert_trades_writes_every_row(cur):
    run_id = insert_run(cur, **RUN)
    trades = [
        Trade(
            stock_id=STOCK,
            entry_at=datetime(2025, 3, 4, 0, 0, tzinfo=UTC),
            entry_price=Decimal(10000),
            quantity=10,
            exit_at=datetime(2025, 3, 5, 0, 0, tzinfo=UTC),
            exit_price=Decimal(11000),
            pnl=Decimal(9500),
            pnl_rate=Decimal("0.0950"),
            exit_reason="timeout",
            payload={"reason": "dummy"},
        ),
        # 미청산. 손익 칸이 비어 있다
        Trade(
            stock_id=STOCK,
            entry_at=datetime(2025, 3, 31, 0, 0, tzinfo=UTC),
            entry_price=Decimal(10000),
            quantity=5,
        ),
    ]

    assert insert_trades(cur, run_id, trades) == 2

    cur.execute(
        "SELECT exit_at, exit_reason, signal_payload FROM backtest_trade"
        " WHERE run_id = %s ORDER BY entry_at",
        (run_id,),
    )
    rows = cur.fetchall()
    assert rows[0][1] == "timeout"
    assert rows[0][2] == {"reason": "dummy"}
    assert rows[1][0] is None  # 미청산은 exit_at 이 비어 있다


def test_trades_are_removed_with_the_run(cur):
    """ON DELETE CASCADE. 실행을 지우면 매매도 사라진다."""
    run_id = insert_run(cur, **RUN)
    insert_trades(
        cur,
        run_id,
        [
            Trade(
                stock_id=STOCK,
                entry_at=datetime(2025, 3, 4, tzinfo=UTC),
                entry_price=Decimal(10000),
                quantity=1,
            )
        ],
    )
    cur.execute("DELETE FROM backtest_run WHERE run_id = %s", (run_id,))

    cur.execute("SELECT COUNT(*) FROM backtest_trade WHERE run_id = %s", (run_id,))
    assert cur.fetchone()[0] == 0


def test_insert_trades_accepts_an_empty_list(cur):
    assert insert_trades(cur, insert_run(cur, **RUN), []) == 0


def test_delisted_between_counts_both(cur):
    """폐지 종목 수와 그중 일봉이 남은 수. 생존편향 경고의 근거다."""
    delisted, with_prices = delisted_between(cur, date(2025, 1, 1), date(2025, 12, 31))

    assert delisted >= with_prices >= 0
