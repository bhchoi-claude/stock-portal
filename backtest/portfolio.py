# 백테스트 장부. 현금과 보유를 체결로만 움직인다

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

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

    def adjust(self, stock_id: str, ratio: Decimal) -> None:
        """권리락. 수량과 평단가를 반비례로 바꾼다. **평가액은 그대로다.**

        분할하면 원주가가 기계적으로 반토막 난다. 수량을 함께 늘리지 않으면
        평가액이 그날 증발하고, 평단가를 함께 줄이지 않으면 손절이 대량
        발동한다 (PROJECT.md 11장 수정주가).

        단주는 모사하지 않는다. 반올림하되 **원가 총액을 보존한다.**
        실제로는 현금 정산되지만 백테스트 근사로 받아들인다.
        """
        held = self.positions[stock_id]
        quantity = int((held.quantity * ratio).to_integral_value(ROUND_HALF_UP))
        if quantity <= 0:
            # 한 주 밑으로 줄어드는 감자. 전부 단주가 되지만 값을 버리지 않는다
            quantity = 1

        cost = held.avg_price * held.quantity
        self.positions[stock_id] = Position(
            account_id=self.account_id,
            stock_id=stock_id,
            quantity=quantity,
            avg_price=cost / quantity,
        )

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
