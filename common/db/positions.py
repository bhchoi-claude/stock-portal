# position 테이블 접근. 증권사 잔고가 정본이고 이 테이블은 캐시다 (SCHEMA.md 5장)

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import psycopg

from ..types import Position


@dataclass(frozen=True)
class Mismatch:
    """DB 와 증권사 잔고가 어긋난 한 종목. 수량만 본다.

    평단가는 수수료 포함 여부와 반올림으로 미세하게 갈릴 수 있어 불일치
    판정에 쓰지 않는다. 수량이 다른 것은 원장이 어긋난 것이다.
    """

    stock_id: str
    db_quantity: int
    broker_quantity: int


def list_positions(cur: psycopg.Cursor, account_id: str) -> list[Position]:
    """DB 에 기록된 보유 종목."""
    cur.execute(
        "SELECT account_id, stock_id, quantity, avg_price, currency"
        " FROM position WHERE account_id = %s ORDER BY stock_id",
        (account_id,),
    )
    return [Position(*row) for row in cur.fetchall()]


def sync_positions(
    cur: psycopg.Cursor, account_id: str, broker_positions: list[Position]
) -> list[Mismatch]:
    """증권사 잔고로 DB 를 맞추고 **어긋난 것을 돌려준다.**

    맞추는 것과 알리는 것을 한 함수에서 한다. `SCHEMA.md` 가 "불일치가
    발견되면 증권사 값으로 덮어쓰고 경고를 남긴다" 고 정해뒀는데, 덮어쓰기
    전에 비교해야 무엇이 달랐는지 알 수 있기 때문이다.

    **호출부가 반환값을 보고 진입을 막는다.** 청산은 막지 않는다 —
    들고 있는 종목의 손절이 원장 불일치로 묶이면 그쪽이 더 위험하다
    (`INTERFACES.md` 4.1).

    `opened_at` 은 건드리지 않는다. 새로 생긴 행은 NULL 이다 — 증권사
    잔고가 최초 취득 시각을 주지 않는다. NULL 은 없다는 뜻이 아니라
    모른다는 뜻이다 (`SCHEMA.md` 5장).
    """
    held = {p.stock_id: p.quantity for p in list_positions(cur, account_id)}
    incoming = {p.stock_id: p.quantity for p in broker_positions}

    mismatches = [
        Mismatch(stock_id, held.get(stock_id, 0), incoming.get(stock_id, 0))
        for stock_id in sorted(held.keys() | incoming.keys())
        if held.get(stock_id, 0) != incoming.get(stock_id, 0)
    ]

    for position in broker_positions:
        cur.execute(
            """
            INSERT INTO position
                (account_id, stock_id, quantity, avg_price, currency, synced_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (account_id, stock_id) DO UPDATE SET
                quantity  = EXCLUDED.quantity,
                avg_price = EXCLUDED.avg_price,
                currency  = EXCLUDED.currency,
                synced_at = NOW()
            """,
            (
                account_id,
                position.stock_id,
                position.quantity,
                position.avg_price,
                position.currency,
            ),
        )

    # 증권사에 없는 종목은 판 것이다. 남겨두면 전략이 없는 포지션을 관리한다
    cur.execute(
        "DELETE FROM position WHERE account_id = %s AND NOT (stock_id = ANY(%s))",
        (account_id, list(incoming)),
    )
    return mismatches


@dataclass(frozen=True)
class PositionView:
    """화면이 보는 보유 종목. 종목명과 마지막 종가를 함께 준다.

    종가는 `price_daily` 의 마지막 값이라 **장중에는 전날 종가다.** 포털은
    브로커를 부르지 않는다 — 통신은 DB 로만 한다 (CLAUDE.md 8). 실시간
    평가액이 필요하면 엔진이 `daily_pnl` 에 남긴 값을 본다.
    """

    stock_id: str
    name: str | None
    quantity: int
    avg_price: Decimal
    last_close: Decimal | None
    opened_at: datetime | None
    synced_at: datetime


def position_views(cur: psycopg.Cursor, account_id: str) -> list[PositionView]:
    """보유 종목에 종목명과 마지막 종가를 붙인다.

    종가는 종목마다 따로 뽑는다. `price_daily` 의 PK 가 `(stock_id,
    trade_date)` 라 종목당 한 행을 집는 것은 인덱스를 그대로 탄다.
    """
    cur.execute(
        "SELECT p.stock_id, s.name, p.quantity, p.avg_price, d.close,"
        " p.opened_at, p.synced_at"
        " FROM position p"
        " LEFT JOIN stock s ON s.stock_id = p.stock_id"
        " LEFT JOIN LATERAL ("
        "   SELECT close FROM price_daily WHERE stock_id = p.stock_id"
        "   ORDER BY trade_date DESC LIMIT 1"
        " ) d ON TRUE"
        " WHERE p.account_id = %s ORDER BY p.stock_id",
        (account_id,),
    )
    return [PositionView(*row) for row in cur.fetchall()]
