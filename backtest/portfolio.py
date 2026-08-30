# 백테스트 장부. 현금과 보유를 체결로만 움직인다

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from common.types import Position, Side

from .execution import Fill


@dataclass
class Portfolio:
    """현금과 포지션. **체결 없이는 아무것도 바뀌지 않는다.**

    평단가에 수수료를 포함한다. 현금이 그만큼 줄었으므로 그것이 실제 원가다.
    포함하지 않으면 손익이 수수료만큼 낙관 쪽으로 틀어진다.
    """

    account_id: str
    cash: Decimal
    positions: dict[str, Position] = field(default_factory=dict)

    def apply(self, fill: Fill) -> None:
        self.cash += fill.cash
        if fill.side is Side.BUY:
            self._add(fill)
        else:
            self._reduce(fill)

    def equity(self, prices: dict[str, Decimal]) -> Decimal:
        """현금 + 평가금액. 값이 없는 종목은 평단가로 본다."""
        return self.cash + self.eval_amount(prices)

    def eval_amount(self, prices: dict[str, Decimal]) -> Decimal:
        return sum(
            (
                prices.get(stock_id, position.avg_price) * position.quantity
                for stock_id, position in self.positions.items()
            ),
            Decimal(0),
        )

    def _add(self, fill: Fill) -> None:
        held = self.positions.get(fill.stock_id)
        cost = fill.gross + fill.fee
        quantity = fill.quantity
        if held is not None:
            cost += held.avg_price * held.quantity
            quantity += held.quantity

        self.positions[fill.stock_id] = Position(
            account_id=self.account_id,
            stock_id=fill.stock_id,
            quantity=quantity,
            avg_price=cost / quantity,
        )

    def _reduce(self, fill: Fill) -> None:
        held = self.positions[fill.stock_id]
        left = held.quantity - fill.quantity
        if left <= 0:
            del self.positions[fill.stock_id]
            return

        self.positions[fill.stock_id] = Position(
            account_id=self.account_id,
            stock_id=fill.stock_id,
            quantity=left,
            avg_price=held.avg_price,
        )
