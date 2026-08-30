# Strategy 규격. 전략의 내용이 아니라 껍데기만 정의한다 (INTERFACES.md 4장)

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..feed.base import DataFeed
from ..types import Balance, Position, Side


@dataclass
class Context:
    """전략이 판단에 쓰는 것 전부. 여기 없는 것은 보지 않는다."""

    feed: DataFeed
    account_id: str
    params: dict[str, Any]
    positions: dict[str, Position]
    balance: Balance


@dataclass(frozen=True)
class EntryIntent:
    """진입 의도. **주문이 아니다.**

    수량이 없는 것이 핵심이다. 포지션 크기는 `RiskManager` 가 정한다 (4.2).
    """

    stock_id: str
    side: Side
    strength: Decimal  # 0~100
    payload: dict[str, Any] = field(default_factory=dict)  # 진입 근거. 자유 형식
    limit_price: Decimal | None = None


@dataclass(frozen=True)
class ExitIntent:
    """청산 의도. 부분 청산이 가능하도록 수량을 받는다."""

    stock_id: str
    quantity: int
    reason: str  # 'target' | 'stop' | 'timeout' | 'signal'
    limit_price: Decimal | None = None


class Strategy(ABC):
    """전략은 **의도만 반환한다.** 주문은 엔진이 낸다.

    포지션 크기 계산, 리스크 한도 확인, 주문 실행은 전략의 책임이 아니다.
    이 분리 덕분에 같은 코드가 백테스트에서도 그대로 돈다 (4.2).

    파라미터는 `ctx.params` 로만 본다. 코드에 숫자를 쓰지 않는다 (4.3).
    """

    name: str  # 'daytrade' | 'swing'

    @abstractmethod
    def scan(self, ctx: Context) -> list[EntryIntent]:
        """진입 후보 탐색."""

    @abstractmethod
    def manage(self, ctx: Context, position: Position) -> ExitIntent | None:
        """보유 포지션의 청산 판단. 포지션마다 호출된다.

        `scan()` 과 독립적으로 매 주기 호출된다. 신규 진입을 막은 상태에서도
        청산은 계속 동작해야 하기 때문이다 (4.1).
        """

    def on_start(self, ctx: Context) -> None:
        """실행 시작 시 한 번."""

    def on_day_end(self, ctx: Context) -> None:
        """거래일 종료 시."""
