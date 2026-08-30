# 백테스트 결과(backtest_run, backtest_trade) 적재와 조회 (SCHEMA.md 7장)

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


def insert_run(
    cur: psycopg.Cursor,
    *,
    strategy: str,
    from_date: date,
    to_date: date,
    universe: str,
    params: dict[str, Any],
    initial_capital: Decimal,
    final_capital: Decimal,
    total_return: Decimal,
    mdd: Decimal,
    win_rate: Decimal | None,
    trade_count: int,
    sharpe: Decimal | None,
    fee_rate: Decimal,
    slippage_rate: Decimal,
    note: str,
) -> int:
    """실행 한 건을 남기고 `run_id` 를 낸다.

    `fee_rate` 와 `slippage_rate` 는 `params` 안에도 있지만 컬럼으로 또 받는다.
    **0 으로 돌린 결과도 그 사실이 남게 하려는 것이다** (SCHEMA.md 7장).
    """
    cur.execute(
        """
        INSERT INTO backtest_run (
            strategy, from_date, to_date, universe, params,
            initial_capital, final_capital, total_return, mdd,
            win_rate, trade_count, sharpe, fee_rate, slippage_rate, note
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING run_id
        """,
        (
            strategy,
            from_date,
            to_date,
            universe,
            Jsonb(params),
            initial_capital,
            final_capital,
            total_return,
            mdd,
            win_rate,
            trade_count,
            sharpe,
            fee_rate,
            slippage_rate,
            note,
        ),
    )
    return cur.fetchone()[0]


def insert_trades(cur: psycopg.Cursor, run_id: int, trades: Sequence[Any]) -> int:
    """매매를 한꺼번에 넣는다. `run` 과 같은 트랜잭션 안에서 불러야 한다.

    갈라지면 매매 없는 실행 기록이 남는다. 지표만 있고 근거가 없는 행이다.
    """
    if not trades:
        return 0
    cur.executemany(
        """
        INSERT INTO backtest_trade (
            run_id, stock_id, entry_at, entry_price, exit_at, exit_price,
            quantity, pnl, pnl_rate, exit_reason, signal_payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        [
            (
                run_id,
                t.stock_id,
                t.entry_at,
                t.entry_price,
                t.exit_at,
                t.exit_price,
                t.quantity,
                t.pnl,
                t.pnl_rate,
                t.exit_reason,
                Jsonb(t.payload) if t.payload else None,
            )
            for t in trades
        ],
    )
    return len(trades)


def delisted_between(cur: psycopg.Cursor, start: date, end: date) -> tuple[int, int]:
    """구간에 폐지된 종목 수와 **그중 일봉이 남아 있는 수**.

    생존편향 경고의 근거다. 둘의 차이가 결과에서 빠진 손실이다.
    """
    cur.execute(
        """
        SELECT COUNT(*),
               COUNT(*) FILTER (
                   WHERE EXISTS (
                       SELECT 1 FROM price_daily p
                       WHERE p.stock_id = s.stock_id
                         AND p.trade_date BETWEEN %s AND %s
                   )
               )
        FROM stock s
        WHERE s.delisted_at BETWEEN %s AND %s
        """,
        (start, end, start, end),
    )
    return cur.fetchone()


@dataclass(frozen=True)
class RunRow:
    """`backtest_run` 한 행. 화면이 쓰는 칸만 담는다."""

    run_id: int
    strategy: str
    from_date: date
    to_date: date
    initial_capital: Decimal
    final_capital: Decimal | None
    total_return: Decimal | None
    mdd: Decimal | None
    win_rate: Decimal | None
    trade_count: int | None
    sharpe: Decimal | None
    fee_rate: Decimal
    slippage_rate: Decimal
    note: str | None
    created_at: datetime


def recent_runs(cur: psycopg.Cursor, limit: int) -> list[RunRow]:
    """최근 실행부터. 지표를 나란히 놓고 비교하는 화면이 쓴다.

    `params` 는 크고 화면에서 읽지 않으므로 뽑지 않는다. **`fee_rate` 와
    `slippage_rate` 는 뽑는다.** 어떤 비용으로 돌린 결과인지 모르면
    두 실행의 지표를 견줄 수 없다.
    """
    cur.execute(
        """
        SELECT run_id, strategy, from_date, to_date, initial_capital,
               final_capital, total_return, mdd, win_rate, trade_count,
               sharpe, fee_rate, slippage_rate, note, created_at
        FROM backtest_run ORDER BY run_id DESC LIMIT %s
        """,
        (limit,),
    )
    return [RunRow(*row) for row in cur.fetchall()]
