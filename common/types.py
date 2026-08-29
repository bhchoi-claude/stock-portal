# 모듈 경계를 오가는 공통 타입. INTERFACES.md 1장 규격이다

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class Regime(str, Enum):
    DANGER = "danger"
    NEUTRAL = "neutral"
    SAFE = "safe"


@dataclass(frozen=True)
class Candle:
    stock_id: str
    ts: datetime  # UTC. 봉의 시작 시각
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class Quote:
    stock_id: str
    ts: datetime  # UTC
    price: Decimal
    bid: Decimal | None
    ask: Decimal | None
    volume: int


@dataclass(frozen=True)
class Position:
    account_id: str
    stock_id: str
    quantity: int
    avg_price: Decimal
    currency: str = "KRW"


@dataclass(frozen=True)
class Balance:
    account_id: str
    deposit: Decimal  # 예수금
    available: Decimal  # 주문가능금액
    eval_amount: Decimal  # 평가금액
    total_asset: Decimal
    currency: str = "KRW"


@dataclass(frozen=True)
class InvestorFlow:
    """투자자별 순매수 금액. 단위는 원이다.

    출처가 주는 값은 백만원 단위라 어댑터가 원으로 바꾼다. 같은 금액 컬럼인
    `price_daily.value` 가 원 단위이므로 맞춘다.
    """

    stock_id: str
    trade_date: date
    foreign_net: Decimal
    institution_net: Decimal
    individual_net: Decimal


@dataclass(frozen=True)
class IndexClose:
    """지수의 일별 종가. 지표 계산에 종가만 쓰므로 OHLC 를 담지 않는다."""

    index_code: str
    trade_date: date
    close: Decimal


@dataclass(frozen=True)
class StockState:
    """종목의 관리종목·거래정지 여부. 어느 시점인지는 담지 않는다.

    출처가 현재 상태만 주고 기준일자를 받지 않는다. 언제의 값인지는
    수집기가 적재 시점으로 정한다.
    """

    stock_id: str
    is_managed: bool
    is_suspended: bool
