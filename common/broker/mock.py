# 테스트와 개발용 목 브로커. 실계좌에 닿지 않는다

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from ..types import Balance, Candle, OrderType, Position, Quote
from .base import Broker, OrderRequest, OrderResult
from .errors import PermanentError


class MockBroker(Broker):
    """미리 넣어둔 값을 돌려주고 주문을 즉시 체결시킨다.

    실계좌 보호를 위해 테스트는 이 목이나 모의투자 계좌로만 돌린다
    (`CLAUDE.md` 테스트 규칙).
    """

    name = "mock"

    def __init__(
        self,
        quotes: dict[str, Quote] | None = None,
        candles: dict[str, list[Candle]] | None = None,
        balance: Balance | None = None,
        positions: list[Position] | None = None,
    ) -> None:
        self._quotes = quotes or {}
        self._candles = candles or {}
        self._balance = balance
        self._positions = positions or []
        self._orders: dict[str, OrderResult] = {}
        self._by_broker_no: dict[str, str] = {}
        self._seq = 0

    # ---- 조회 ----

    def get_quote(self, stock_id: str) -> Quote:
        if stock_id not in self._quotes:
            raise PermanentError(f"{stock_id} 시세가 없습니다.")
        return self._quotes[stock_id]

    def get_candles(
        self,
        stock_id: str,
        interval: str,
        count: int,
        end: datetime | None = None,
    ) -> list[Candle]:
        candles = self._candles.get(stock_id, [])
        if end is not None:
            candles = [c for c in candles if c.ts <= end]
        return candles[-count:]

    def get_balance(self, account_id: str) -> Balance:
        if self._balance is None:
            raise PermanentError(f"{account_id} 잔고가 없습니다.")
        return self._balance

    def get_positions(self, account_id: str) -> list[Position]:
        return [p for p in self._positions if p.account_id == account_id]

    # ---- 주문 ----

    def submit_order(self, req: OrderRequest) -> OrderResult:
        # 중복 client_order_id 는 DB 의 UNIQUE 가 1차로 막지만, 목도 막아야
        # 주문 흐름 테스트가 브로커 경계까지 확인할 수 있다 (INTERFACES.md 2.1)
        if req.client_order_id in self._orders:
            raise PermanentError(
                f"이미 접수된 client_order_id 입니다: {req.client_order_id}"
            )

        self._seq += 1
        broker_order_no = f"MOCK{self._seq:06d}"
        price = req.price
        if req.order_type is OrderType.MARKET:
            price = self._quotes[req.stock_id].price

        result = OrderResult(
            client_order_id=req.client_order_id,
            broker_order_no=broker_order_no,
            status="filled",
            filled_qty=req.quantity,
            avg_fill_price=price,
        )
        self._orders[req.client_order_id] = result
        self._by_broker_no[broker_order_no] = req.client_order_id
        return result

    def cancel_order(self, account_id: str, broker_order_no: str) -> OrderResult:
        result = self.get_order_status(account_id, broker_order_no)
        cancelled = OrderResult(
            client_order_id=result.client_order_id,
            broker_order_no=broker_order_no,
            status="cancelled",
            filled_qty=result.filled_qty,
            avg_fill_price=result.avg_fill_price,
        )
        self._orders[result.client_order_id] = cancelled
        return cancelled

    def get_order_status(self, account_id: str, broker_order_no: str) -> OrderResult:
        client_order_id = self._by_broker_no.get(broker_order_no)
        if client_order_id is None:
            raise PermanentError(f"모르는 주문번호입니다: {broker_order_no}")
        return self._orders[client_order_id]

    # ---- 실시간 ----

    def subscribe(
        self, stock_ids: list[str], on_quote: Callable[[Quote], None]
    ) -> None:
        for stock_id in stock_ids:
            if stock_id in self._quotes:
                on_quote(self._quotes[stock_id])

    def unsubscribe(self, stock_ids: list[str]) -> None:
        return None
