# 기준 데이터 테이블의 행을 나타내는 dataclass 모음

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal


def make_stock_id(exchange: str, code: str) -> str:
    """종목 식별자를 만든다. 접두어는 거래소이며 시장 구분(KOSPI/KOSDAQ)이 아니다."""
    return f"{exchange}:{code}"


@dataclass(frozen=True)
class Exchange:
    exchange: str
    name: str
    country: str
    currency: str
    timezone: str
    open_time: time
    close_time: time
    is_active: bool = True


@dataclass(frozen=True)
class Holiday:
    exchange: str
    holiday_date: date
    name: str | None = None


@dataclass(frozen=True)
class Stock:
    stock_id: str
    exchange: str
    code: str
    board: str
    name: str
    sector: str | None = None
    listed_shares: int | None = None
    is_managed: bool = False
    is_suspended: bool = False
    is_spac: bool = False
    is_preferred: bool = False
    listed_at: date | None = None
    delisted_at: date | None = None


@dataclass(frozen=True)
class StockStatus:
    stock_id: str
    valid_from: date
    board: str
    is_managed: bool = False
    is_suspended: bool = False
    valid_to: date | None = None


@dataclass(frozen=True)
class PriceDaily:
    stock_id: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    value: Decimal | None = None
    # corporate_action 에서 계산하는 파생값이다. 수집기는 채우지 않는다
    adj_factor: Decimal = Decimal(1)


@dataclass(frozen=True)
class CorporateAction:
    stock_id: str
    effective_date: date
    action_type: str
    adjusts_price: bool
    # 상장주식수 비(이후/이전). 50:1 감자면 0.02, 1:2 액면분할이면 2.0 이다.
    # 이 날 이전 가격에 곱할 조정계수는 이 값의 역수를 누적한 것이다
    ratio: Decimal | None = None
    source: str | None = None
    detail: dict | None = None


@dataclass(frozen=True)
class Account:
    account_id: str
    broker: str
    strategy: str
    is_paper: bool = False
    currency: str = "KRW"
    is_active: bool = True


@dataclass(frozen=True)
class Source:
    kind: str
    identifier: str
    name: str
    weight: Decimal = Decimal("1.0")
    is_active: bool = True
