# 목 브로커가 브로커 규격대로 동작하는지 확인한다

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from common.broker.base import OrderRequest
from common.broker.errors import PermanentError
from common.broker.mock import MockBroker
from common.types import Balance, Candle, OrderType, Quote, Side

TS = datetime(2026, 8, 26, 6, 0, tzinfo=UTC)
STOCK = "KRX:005930"


def quote(price: str) -> Quote:
    return Quote(
        stock_id=STOCK,
        ts=TS,
        price=Decimal(price),
        bid=Decimal(price),
        ask=Decimal(price),
        volume=10,
    )


def candle(minute: int, close: str) -> Candle:
    return Candle(
        stock_id=STOCK,
        ts=datetime(2026, 8, 26, 6, minute, tzinfo=UTC),
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=1,
    )


def order(client_order_id: str, order_type: OrderType = OrderType.LIMIT):
    return OrderRequest(
        client_order_id=client_order_id,
        account_id="paper",
        stock_id=STOCK,
        side=Side.BUY,
        order_type=order_type,
        quantity=10,
        price=Decimal(70000) if order_type is OrderType.LIMIT else None,
    )


def test_중복_client_order_id_는_차단된다():
    # INTERFACES.md 2.1. 응답을 못 받아 재시도해도 중복 주문이 되면 안 된다
    broker = MockBroker(quotes={STOCK: quote("70000")})
    broker.submit_order(order("01J0"))

    with pytest.raises(PermanentError):
        broker.submit_order(order("01J0"))


def test_다른_주문번호는_통과한다():
    broker = MockBroker(quotes={STOCK: quote("70000")})

    first = broker.submit_order(order("01J0"))
    second = broker.submit_order(order("01J1"))

    assert first.broker_order_no != second.broker_order_no


def test_봉은_시간_오름차순으로_돌려준다():
    broker = MockBroker(candles={STOCK: [candle(0, "100"), candle(1, "101")]})

    got = broker.get_candles(STOCK, "1m", 10)

    assert [c.close for c in got] == [Decimal(100), Decimal(101)]


def test_end_이후_봉은_돌려주지_않는다():
    # 미래 참조를 막는다. 피드가 이 성질에 의존한다
    broker = MockBroker(candles={STOCK: [candle(0, "100"), candle(5, "105")]})

    got = broker.get_candles(
        STOCK, "1m", 10, end=datetime(2026, 8, 26, 6, 1, tzinfo=UTC)
    )

    assert [c.close for c in got] == [Decimal(100)]


def test_count_만큼만_최근_봉을_돌려준다():
    broker = MockBroker(candles={STOCK: [candle(i, str(100 + i)) for i in range(5)]})

    got = broker.get_candles(STOCK, "1m", 2)

    assert [c.close for c in got] == [Decimal(103), Decimal(104)]


def test_시장가는_현재가로_체결된다():
    broker = MockBroker(quotes={STOCK: quote("71000")})

    result = broker.submit_order(order("01J2", OrderType.MARKET))

    assert result.avg_fill_price == Decimal(71000)
    assert result.status == "filled"


def test_모르는_종목_시세는_permanent_error():
    with pytest.raises(PermanentError):
        MockBroker().get_quote("KRX:999999")


def test_주문_취소가_상태를_바꾼다():
    broker = MockBroker(quotes={STOCK: quote("70000")})
    submitted = broker.submit_order(order("01J3"))

    cancelled = broker.cancel_order("paper", submitted.broker_order_no)

    assert cancelled.status == "cancelled"
    assert (
        broker.get_order_status("paper", submitted.broker_order_no).status
        == "cancelled"
    )


def test_잔고가_없으면_permanent_error():
    with pytest.raises(PermanentError):
        MockBroker().get_balance("paper")


def test_계좌별로_보유종목을_거른다():
    from common.types import Position

    mine = Position(
        account_id="paper", stock_id=STOCK, quantity=1, avg_price=Decimal(1)
    )
    other = Position(
        account_id="swing", stock_id=STOCK, quantity=2, avg_price=Decimal(1)
    )
    broker = MockBroker(positions=[mine, other])

    assert broker.get_positions("paper") == [mine]


def test_잔고를_그대로_돌려준다():
    balance = Balance(
        account_id="paper",
        deposit=Decimal(1000),
        available=Decimal(900),
        eval_amount=Decimal(100),
        total_asset=Decimal(1100),
    )

    assert MockBroker(balance=balance).get_balance("paper") == balance
