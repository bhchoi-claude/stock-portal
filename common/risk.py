# RiskManager. 진입 의도를 주문 수량으로 바꾸는 계층 (INTERFACES.md 5장)

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .strategy.base import Context, EntryIntent
from .types import Regime


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    quantity: int
    reason: str | None = None  # 거부 사유


class RiskManager:
    """한도를 한곳에서 강제한다.

    전략은 수량을 정하지 않는다. 여기서만 정한다. 그래야 전략이 늘어나도
    한도가 흩어지지 않는다 (INTERFACES.md 4.2).
    """

    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params
        self._day_start_asset: Decimal | None = None

    def start_day(self, total_asset: Decimal) -> None:
        """그날 시작 자산을 기억한다. 일일 손실 한도의 기준이다."""
        self._day_start_asset = total_asset

    def evaluate(
        self, intent: EntryIntent, ctx: Context, regime: Regime
    ) -> RiskDecision:
        """포지션 크기 산정과 한도 확인. 거부하면 이유를 남긴다."""
        if self._hit_daily_loss(ctx):
            # 전략이 뭘 하든 그날은 더 사지 않는다
            return RiskDecision(False, 0, "daily_loss_limit")

        if len(ctx.positions) >= self.params["max_positions"]:
            return RiskDecision(False, 0, "max_positions")

        budget = self._budget(ctx, regime)
        if budget <= 0:
            return RiskDecision(False, 0, "no_budget")

        price = intent.limit_price or ctx.feed.get_quote(intent.stock_id).price
        if price <= 0:
            return RiskDecision(False, 0, "no_price")

        quantity = int(budget / price)
        if quantity <= 0:
            # 한 주도 못 사는 금액이면 사지 않는다
            return RiskDecision(False, 0, "too_small")

        return RiskDecision(True, quantity)

    def _hit_daily_loss(self, ctx: Context) -> bool:
        if self._day_start_asset is None or self._day_start_asset <= 0:
            return False
        loss = (self._day_start_asset - ctx.balance.total_asset) / self._day_start_asset
        return loss >= Decimal(str(self.params["daily_loss_limit"]))

    def _budget(self, ctx: Context, regime: Regime) -> Decimal:
        """이번 한 건에 쓸 수 있는 금액. 네 가지 중 가장 작은 값이다.

        국면 배분은 **총 투입 한도**라서 이미 들고 있는 금액을 뺀다.
        빼지 않으면 위험 국면에서도 종목 수만큼 계속 살 수 있다.
        """
        allocation = Decimal(str(self.params["regime_allocation"][regime.value]))
        total = ctx.balance.total_asset
        invested = sum(
            (
                position.avg_price * position.quantity
                for position in ctx.positions.values()
            ),
            Decimal(0),
        )

        return min(
            total * allocation - invested,
            Decimal(str(self.params["max_position_size"])),
            total * Decimal(str(self.params["max_weight_per_stock"])),
            ctx.balance.available,
        )
