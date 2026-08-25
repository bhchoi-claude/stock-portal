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
