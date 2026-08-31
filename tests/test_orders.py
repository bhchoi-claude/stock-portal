# 주문 기록과 중복 차단. DB 통합 테스트다 (흐름 테스트만 실제로 커밋한다)

from decimal import Decimal

import pytest

from common.broker.base import OrderResult
from common.broker.mock import MockBroker
from common.db.orders import (
    DuplicateOrderError,
    apply_result,
    list_open_orders,
    record_pending,
)
from common.order import CROCKFORD, new_client_order_id, place_order
from common.types import OrderType, Side

# 모의투자 계좌와 시드에 있는 종목을 쓴다. 실계좌 ID 를 쓰지 않는다
ACCOUNT = "paper"
STOCK = "KRX:005930"

ORDER = {
    "account_id": ACCOUNT,
    "stock_id": STOCK,
    "side": Side.BUY,
    "order_type": OrderType.LIMIT,
    "quantity": 10,
    "price": Decimal(180000),
}


# ---- client_order_id ----


def test_ulid_는_26자다():
    assert len(new_client_order_id()) == 26


def test_ulid_는_crockford_문자만_쓴다():
    """I·L·O·U 가 없어야 눈으로 옮겨 적어도 헷갈리지 않는다."""
    value = new_client_order_id()

    assert set(value) <= set(CROCKFORD)
    assert not set(value) & set("ILOU")


def test_ulid_첫_글자가_7_을_넘지_않는다():
    """26자 × 5비트 = 130비트인데 값은 128비트다. 앞 2비트는 항상 0 이다."""
    assert CROCKFORD.index(new_client_order_id()[0]) <= 7


def test_ulid_는_시간순으로_늘어난다():
    first = new_client_order_id()
    second = new_client_order_id()

    # 같은 밀리초면 난수부에서 갈리므로 앞 10자(타임스탬프)만 비교한다
    assert first[:10] <= second[:10]


def test_ulid_가_겹치지_않는다():
    assert len({new_client_order_id() for _ in range(1000)}) == 1000


# ---- 기록 ----


def test_주문을_pending_으로_먼저_기록한다(cur):
    order_id = record_pending(cur, client_order_id=new_client_order_id(), **ORDER)

    cur.execute(
        "SELECT status, quantity, price, filled_qty FROM order_request"
        " WHERE order_id = %s",
        (order_id,),
    )
    status, quantity, price, filled_qty = cur.fetchone()
    assert status == "pending"
    assert quantity == 10
    assert price == Decimal(180000)
    assert filled_qty == 0


def test_중복_client_order_id_가_차단된다(cur):
    """CLAUDE.md 필수 테스트. 증권사는 우리 키를 모르므로 이 행 하나가
    멱등성의 전부다."""
    client_order_id = new_client_order_id()
    record_pending(cur, client_order_id=client_order_id, **ORDER)

    with pytest.raises(DuplicateOrderError):
        record_pending(cur, client_order_id=client_order_id, **ORDER)


# ---- 갱신 ----


def test_응답으로_주문을_갱신한다(cur):
    client_order_id = new_client_order_id()
    order_id = record_pending(cur, client_order_id=client_order_id, **ORDER)

    apply_result(
        cur,
        OrderResult(
            client_order_id=client_order_id,
            broker_order_no="0060327",
            status="filled",
            filled_qty=10,
            avg_fill_price=Decimal(179950),
        ),
    )

    cur.execute(
        "SELECT status, broker_order_no, filled_qty, avg_fill_price"
        " FROM order_request WHERE order_id = %s",
        (order_id,),
    )
    assert cur.fetchone() == ("filled", "0060327", 10, Decimal(179950))


def test_체결량이_줄어들지_않는다(cur):
    """취소 응답에는 체결량이 없어 cancel_order 가 0 을 돌려준다.

    그대로 쓰면 부분체결된 주문을 취소한 순간 체결 기록이 사라진다.
    """
    client_order_id = new_client_order_id()
    order_id = record_pending(cur, client_order_id=client_order_id, **ORDER)
    apply_result(
        cur,
        OrderResult(
            client_order_id=client_order_id,
            broker_order_no="0060327",
            status="partial",
            filled_qty=4,
            avg_fill_price=Decimal(179950),
        ),
    )

    apply_result(
        cur,
        OrderResult(
            client_order_id=client_order_id,
            broker_order_no=None,
            status="cancelled",
            filled_qty=0,
            avg_fill_price=None,
        ),
    )

    cur.execute(
        "SELECT status, broker_order_no, filled_qty, avg_fill_price"
        " FROM order_request WHERE order_id = %s",
        (order_id,),
    )
    status, broker_order_no, filled_qty, avg_fill_price = cur.fetchone()
    assert status == "cancelled"
    assert filled_qty == 4
    assert avg_fill_price == Decimal(179950)
    # 취소 응답에 번호가 없어도 받아둔 번호를 잃지 않는다
    assert broker_order_no == "0060327"


# ---- 재시작 복구 ----


def test_끝나지_않은_주문만_돌려준다(cur):
    open_ids = []
    for status in ("pending", "submitted", "partial", "filled", "cancelled"):
        client_order_id = new_client_order_id()
        record_pending(cur, client_order_id=client_order_id, **ORDER)
        if status != "pending":
            apply_result(
                cur,
                OrderResult(
                    client_order_id=client_order_id,
                    broker_order_no="0060327",
                    status=status,
                    filled_qty=0,
                    avg_fill_price=None,
                ),
            )
        if status in ("pending", "submitted", "partial"):
            open_ids.append(client_order_id)

    found = {o.client_order_id for o in list_open_orders(cur, ACCOUNT)}
    assert set(open_ids) <= found
    assert len(open_ids) == 3


# ---- 흐름 ----


class _RaisingBroker(MockBroker):
    """응답을 못 받는 상황. 접수됐는지 알 수 없다."""

    def submit_order(self, req):
        raise TimeoutError("응답 없음")


def test_주문_실패해도_기록은_남는다(db_conn):
    """기록을 커밋한 뒤에 주문을 내기 때문이다.

    같은 트랜잭션에 두면 주문은 나갔는데 롤백되어 기록이 사라진다.
    그러면 다음에 같은 주문을 또 낸다.
    """
    with db_conn.cursor() as cur:
        before = {o.client_order_id for o in list_open_orders(cur, ACCOUNT)}

    with pytest.raises(TimeoutError):
        place_order(db_conn, _RaisingBroker(), **ORDER)

    with db_conn.cursor() as cur:
        added = [
            o for o in list_open_orders(cur, ACCOUNT) if o.client_order_id not in before
        ]
    assert len(added) == 1
    assert added[0].status == "pending"
    assert added[0].quantity == 10


def test_체결되면_기록이_갱신된다(db_conn):
    # 지정가라 시세가 필요 없다. MockBroker 는 req.price 로 체결시킨다
    result = place_order(db_conn, MockBroker(), **ORDER)

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT status, filled_qty FROM order_request WHERE client_order_id = %s",
            (result.client_order_id,),
        )
        assert cur.fetchone() == ("filled", 10)
