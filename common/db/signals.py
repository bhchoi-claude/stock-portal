# signal 테이블 접근. 19:00 계획을 다음 날 08:30 까지 넘기는 자리다

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from ..types import Side, Signal


def record_signal(
    cur: psycopg.Cursor,
    *,
    stock_id: str,
    strategy: str,
    side: Side,
    strength: Decimal | None = None,
    payload: dict[str, Any] | None = None,
    regime_at: str | None = None,
) -> int:
    """계획 한 건을 남긴다. **수량은 담지 않는다.**

    `payload` 는 전략별 근거다 (`SCHEMA.md` 5장). 주문 수량은 근거가 아니라
    운영 값이라 넣지 않는다. 담지 않아도 되는 이유가 더 크다 — 매수 수량은
    08:30 에 `RiskManager` 가 그 시점 주문가능금액으로 다시 뽑고, 매도
    수량은 그때의 보유 수량이다.
    """
    cur.execute(
        """
        INSERT INTO signal (stock_id, strategy, side, strength, payload, regime_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING signal_id
        """,
        (
            stock_id,
            strategy,
            side.value,
            strength,
            Jsonb(payload) if payload else None,
            regime_at,
        ),
    )
    row = cur.fetchone()
    assert row is not None  # RETURNING 이 있으므로 항상 한 행이다
    return int(row[0])


def pending_signals(
    cur: psycopg.Cursor, strategy: str, since: datetime
) -> list[Signal]:
    """아직 주문으로 이어지지 않은 계획. **`since` 보다 새 것만 준다.**

    계획은 하루짜리다. 전날 종가로 정한 것이라 하루가 지나면 근거가 낡는다.
    아침을 한 번 거른 뒤 이틀 전 계획으로 주문을 내면 안 된다.

    낡은 행을 지우거나 소비 처리하지 않는다. `consumed_at` 은 "주문으로
    이어진 시각" 이라 버린 것에 찍으면 거짓이 된다. 하루 몇 건이라 남겨둬도
    부담이 없고, 계획이 있었는데 실행되지 않았다는 기록으로 남는다.
    """
    cur.execute(
        "SELECT signal_id, stock_id, strategy, side, strength, payload,"
        " regime_at, created_at"
        " FROM signal"
        " WHERE strategy = %s AND consumed_at IS NULL AND created_at >= %s"
        " ORDER BY created_at, signal_id",
        (strategy, since),
    )
    return [
        Signal(
            signal_id=row[0],
            stock_id=row[1],
            strategy=row[2],
            side=Side(row[3]),
            strength=row[4],
            payload=row[5],
            regime_at=row[6],
            created_at=row[7],
        )
        for row in cur.fetchall()
    ]


def consume(cur: psycopg.Cursor, signal_id: int) -> None:
    """주문으로 이어졌다고 표시한다.

    **주문을 낸 뒤에 부른다.** 먼저 찍으면 주문이 실패했을 때 계획이
    사라진다. 두 번 찍히지 않도록 `consumed_at IS NULL` 을 조건에 둔다.
    """
    cur.execute(
        "UPDATE signal SET consumed_at = NOW()"
        " WHERE signal_id = %s AND consumed_at IS NULL",
        (signal_id,),
    )
