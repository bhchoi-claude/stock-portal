# 수집기 플러그인 규격. INTERFACES.md 6장이다

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class IndicatorRecord:
    """indicator_value 로 적재한다."""

    indicator_code: str
    period_date: date  # 일간은 해당일, 월간은 월초
    value: Decimal


@dataclass(frozen=True)
class MessageRecord:
    """raw_message 로 적재한다. Phase 5 정보수집이 쓴다."""

    external_id: str | None
    content: str
    published_at: datetime  # UTC


@dataclass(frozen=True)
class CollectResult:
    success: bool
    records: list = field(default_factory=list)
    error: str | None = None
    next_since: datetime | None = None


class Collector(ABC):
    """수집기 하나. 어디서 어떻게 가져오든 규격 형식으로 돌려준다.

    판정 엔진과 분석기는 데이터 출처를 알지 못한다 (INTERFACES.md 6.1).
    """

    source_kind: str  # 'telegram' | 'dart' | 'krx' | 'kofia' | 'customs'
    # source 표의 (kind, identifier) 를 찾아 last_success_at 을 갱신한다
    source_identifier: str
    interval_sec: int

    @abstractmethod
    def collect(self, since: datetime) -> CollectResult: ...
