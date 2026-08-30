# 틀 검증용 더미 전략. 매일 사서 다음 날 판다

from __future__ import annotations

from decimal import Decimal

from ..types import Position, Side
from .base import Context, EntryIntent, ExitIntent, Strategy


class DummyStrategy(Strategy):
    """**매매에 쓰라고 만든 것이 아니다.** 백테스트 틀이 완주하는지 본다.

    유니버스 상위 몇 종목을 사고 다음 주기에 전부 판다. 판단이 없으므로
    결과의 좋고 나쁨에 뜻이 없다. 파이프라인이 도는지만 보여준다.

    실전 전략은 Phase 7 에서 따로 논의한다 (ROADMAP.md).
    """

    name = "dummy"

    def scan(self, ctx: Context) -> list[EntryIntent]:
        universe = ctx.feed.get_universe()[: ctx.params["entries_per_day"]]
        return [
            EntryIntent(
                stock_id=stock_id,
                side=Side.BUY,
                strength=Decimal(50),
                payload={"reason": "dummy"},
            )
            for stock_id in universe
            # 이미 들고 있으면 더 사지 않는다. 더미의 유일한 판단이다
            if stock_id not in ctx.positions
        ]

    def manage(self, ctx: Context, position: Position) -> ExitIntent | None:
        """보유하고 있으면 무조건 판다.

        진입 다음 주기에 호출되므로 하루 보유가 된다.
        """
        return ExitIntent(
            stock_id=position.stock_id,
            quantity=position.quantity,
            reason="timeout",
        )
