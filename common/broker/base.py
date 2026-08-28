# Broker 추상 인터페이스와 주문 타입. INTERFACES.md 2장 규격이다

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ..types import Balance, Candle, OrderType, Position, Quote, Side


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str  # 중복 방지 키. 주문 전에 DB 에 기록한다 (2.1)
    account_id: str
    stock_id: str
    side: Side
    order_type: OrderType
    quantity: int
    price: Decimal | None = None  # MARKET 이면 None


@dataclass(frozen=True)
class OrderResult:
    client_order_id: str
    broker_order_no: str | None
    status: str  # 'submitted'|'partial'|'filled'|'cancelled'|'rejected'
    filled_qty: int
    avg_fill_price: Decimal | None
    error_code: str | None = None
    error_message: str | None = None


class Broker(ABC):
    """증권사 API 추상화.

    구현체는 `stock_id` 를 그대로 증권사에 넘기지 않는다. 접두어를 떼는 변환은
    어댑터 안에만 둔다. 밖으로 새면 전략이 종목코드 형식을 알게 된다.
    """

    name: str

    # ---- 조회 ----

    @abstractmethod
    def get_quote(self, stock_id: str) -> Quote: ...

    @abstractmethod
    def get_candles(
        self,
        stock_id: str,
        interval: str,
        count: int,
        end: datetime | None = None,
    ) -> list[Candle]:
        """interval: '1m' | '5m' | '1d'. 시간 오름차순으로 돌려준다."""

    @abstractmethod
    def get_balance(self, account_id: str) -> Balance: ...

    @abstractmethod
    def get_positions(self, account_id: str) -> list[Position]: ...

    # ---- 주문 ----

    @abstractmethod
    def submit_order(self, req: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def cancel_order(self, account_id: str, broker_order_no: str) -> OrderResult: ...

    @abstractmethod
    def get_order_status(
        self, account_id: str, broker_order_no: str
    ) -> OrderResult: ...

    # ---- 실시간 ----

    @abstractmethod
    def subscribe(
        self, stock_ids: list[str], on_quote: Callable[[Quote], None]
    ) -> None: ...

    @abstractmethod
    def unsubscribe(self, stock_ids: list[str]) -> None: ...
