# 모듈 경계를 오가는 공통 타입. INTERFACES.md 1장 규격이다

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
