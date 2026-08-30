# 체결 시뮬레이터. 수수료·세금·슬리피지를 반영한다 (PROJECT.md 11장)

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any

from common.types import Side

WON = Decimal(1)


@dataclass(frozen=True)
class Fill:
    """체결 한 건. 금액은 전부 원 단위 `Decimal` 이다 (CLAUDE.md 4)."""

    stock_id: str
    day: date
    side: Side
    quantity: int
    price: Decimal  # 슬리피지를 반영한 체결 단가
    gross: Decimal  # 단가 × 수량
    fee: Decimal  # 위탁수수료
    tax: Decimal  # 증권거래세. 매도에만 붙는다
    cash: Decimal  # 현금 증감. 매수는 음수, 매도는 양수


class Executor:
    """**다음 거래일 시가에 체결한다** (2026-08-30 승인).

    종가로 신호를 계산하고 다음 날 시가에 체결하므로 미래 참조가 구조적으로
    불가능하다. 갭이 결과에 그대로 들어오는데 그것도 현실이다.

    호가단위는 모사하지 않는다. 슬리피지를 곱한 단가가 실제 호가에 맞지
    않을 수 있다. 백테스트 근사로 받아들인다.
    """

    def __init__(self, params: dict[str, Any]) -> None:
        self.fee_rate = Decimal(str(params["fee_rate"]))
        self.slippage_rate = Decimal(str(params["slippage_rate"]))
        self.tax_rate = {
            board: Decimal(str(rate)) for board, rate in params["tax_rate"].items()
        }

    def buy(self, stock_id: str, day: date, open_price: Decimal, quantity: int) -> Fill:
        """사는 쪽은 슬리피지만큼 비싸게 산다. 세금은 없다."""
        price = open_price * (Decimal(1) + self.slippage_rate)
        gross = _won(price * quantity)
        fee = _cut(gross * self.fee_rate)

        return Fill(
            stock_id=stock_id,
            day=day,
            side=Side.BUY,
            quantity=quantity,
            price=price,
            gross=gross,
            fee=fee,
            tax=Decimal(0),
            cash=-(gross + fee),
        )

    def sell(
        self,
        stock_id: str,
        day: date,
        open_price: Decimal,
        quantity: int,
        board: str,
    ) -> Fill:
        """파는 쪽은 슬리피지만큼 싸게 판다. **증권거래세가 여기에만 붙는다.**

        세금은 손익과 무관하게 매도금액 기준이다. 손실을 봐도 낸다.
        """
        price = open_price * (Decimal(1) - self.slippage_rate)
        gross = _won(price * quantity)
        fee = _cut(gross * self.fee_rate)
        tax = _cut(gross * self._tax_for(board))

        return Fill(
            stock_id=stock_id,
            day=day,
            side=Side.SELL,
            quantity=quantity,
            price=price,
            gross=gross,
            fee=fee,
            tax=tax,
            cash=gross - fee - tax,
        )

    def _tax_for(self, board: str) -> Decimal:
        """모르는 시장은 가장 높은 세율로 본다. 낙관 쪽으로 틀리지 않는다."""
        if board in self.tax_rate:
            return self.tax_rate[board]
        return max(self.tax_rate.values())


def _won(amount: Decimal) -> Decimal:
    """원 단위로 반올림."""
    return amount.quantize(WON, rounding=ROUND_HALF_UP)


def _cut(amount: Decimal) -> Decimal:
    """원 미만 절사. 수수료와 세금이 그렇게 매겨진다."""
    return amount.quantize(WON, rounding=ROUND_DOWN)
