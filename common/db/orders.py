# order_request 테이블 접근. 기록이 주문보다 먼저다 (INTERFACES.md 2.1)

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import psycopg

from ..broker.base import OrderResult
from ..types import OrderType, Side

# 아직 끝나지 않은 주문. 재시작할 때 증권사와 대조한다 (INTERFACES.md 2.2)
OPEN_STATUSES = ("pending", "submitted", "partial")


class DuplicateOrderError(Exception):
    """같은 `client_order_id` 가 이미 있다. DB 가 중복 주문을 막았다."""


@dataclass(frozen=True)
class OpenOrder:
    """끝나지 않은 주문 한 건. 재시작 복구가 증권사 상태와 맞춰본다."""

    order_id: int
    client_order_id: str
    account_id: str
    stock_id: str
    side: str
    order_type: str
    quantity: int
    price: Decimal | None
    status: str
    broker_order_no: str | None
    filled_qty: int


def record_pending(
    cur: psycopg.Cursor,
    *,
    client_order_id: str,
    account_id: str,
    stock_id: str,
    side: Side,
    order_type: OrderType,
    quantity: int,
    price: Decimal | None = None,
    signal_id: int | None = None,
) -> int:
    """주문을 내기 **전에** 기록한다. `status='pending'`.

    순서가 바뀌면 중복 주문을 막을 수단이 없다. 증권사는 우리
    `client_order_id` 를 모르므로 (보내지도 않는다) 멱등성은 이 행 하나에
    달려 있다.

    UNIQUE 위반이면 `DuplicateOrderError` 다. **이 예외가 나면 트랜잭션이
    이미 깨져 있다** — PostgreSQL 은 제약 위반 뒤 같은 트랜잭션에서
    다음 문장을 받지 않는다. 잡아서 이어가지 말고 트랜잭션을 끝내야 한다.
    """
    try:
        cur.execute(
            """
            INSERT INTO order_request
                (client_order_id, account_id, stock_id, side,
                 order_type, quantity, price, signal_id, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')
            RETURNING order_id
            """,
            (
                client_order_id,
                account_id,
                stock_id,
                side.value,
                order_type.value,
                quantity,
                price,
                signal_id,
            ),
        )
    except psycopg.errors.UniqueViolation as exc:
        raise DuplicateOrderError(
            "이미 기록된 client_order_id 입니다: " + client_order_id
        ) from exc

    row = cur.fetchone()
    assert row is not None  # RETURNING 이 있으므로 항상 한 행이다
    return int(row[0])


def apply_result(cur: psycopg.Cursor, result: OrderResult) -> None:
    """브로커 응답을 반영한다. `client_order_id` 로 찾는다.

    **체결량은 줄어들지 않는다** (`GREATEST`). 취소 응답에는 체결량이 없어
    `cancel_order` 가 0 을 돌려주는데, 그대로 쓰면 부분체결된 주문을 취소한
    순간 체결 기록이 사라진다. 늦게 도착한 오래된 응답도 같은 사고를 낸다.
    줄어드는 응답이 정말로 오면 원장이 어긋난 것이니 눈으로 봐야 한다.

    같은 이유로 체결가는 **체결이 있는 응답만** 덮어쓴다.

    주문번호는 `COALESCE` 로 지킨다. 취소·조회 응답에 번호가 없어도 이미
    받아둔 번호를 잃지 않는다.
    """
    cur.execute(
        """
        UPDATE order_request SET
            status          = %s,
            broker_order_no = COALESCE(%s, broker_order_no),
            filled_qty      = GREATEST(filled_qty, %s),
            avg_fill_price  = CASE WHEN %s > 0 THEN %s ELSE avg_fill_price END,
            error_message   = %s,
            updated_at      = NOW()
        WHERE client_order_id = %s
        """,
        (
            result.status,
            result.broker_order_no,
            result.filled_qty,
            result.filled_qty,
            result.avg_fill_price,
            result.error_message,
            result.client_order_id,
        ),
    )


def list_open_orders(cur: psycopg.Cursor, account_id: str) -> list[OpenOrder]:
    """아직 끝나지 않은 주문. 엔진은 이 대조가 끝나기 전에 신규 주문을 내지
    않는다 (`INTERFACES.md` 2.2)."""
    cur.execute(
        "SELECT order_id, client_order_id, account_id, stock_id, side,"
        " order_type, quantity, price, status, broker_order_no, filled_qty"
        " FROM order_request"
        " WHERE account_id = %s AND status = ANY(%s)"
        " ORDER BY created_at",
        (account_id, list(OPEN_STATUSES)),
    )
    return [OpenOrder(*row) for row in cur.fetchall()]


@dataclass(frozen=True)
class OrderView:
    """화면이 보는 주문 한 건. 종목명을 함께 준다."""

    order_id: int
    stock_id: str
    name: str | None
    side: str
    order_type: str
    quantity: int
    price: Decimal | None
    status: str
    filled_qty: int
    avg_fill_price: Decimal | None
    error_message: str | None
    is_manual: bool
    created_at: datetime
    updated_at: datetime


def recent_orders(cur: psycopg.Cursor, account_id: str, limit: int) -> list[OrderView]:
    """최근 주문. 끝난 것도 함께 준다 — 화면은 '오늘 뭐가 나갔나' 를 본다."""
    cur.execute(
        "SELECT o.order_id, o.stock_id, s.name, o.side, o.order_type, o.quantity,"
        " o.price, o.status, o.filled_qty, o.avg_fill_price, o.error_message,"
        " o.is_manual, o.created_at, o.updated_at"
        " FROM order_request o LEFT JOIN stock s ON s.stock_id = o.stock_id"
        " WHERE o.account_id = %s ORDER BY o.created_at DESC, o.order_id DESC"
        " LIMIT %s",
        (account_id, limit),
    )
    return [OrderView(*row) for row in cur.fetchall()]
