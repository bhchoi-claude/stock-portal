# 스윙 추세 추종 전략. **검증 실패. 실전에 쓰지 않는다** (2026-08-30)

from __future__ import annotations

from decimal import Decimal

from ..types import Candle, Position, Side
from .base import Context, EntryIntent, ExitIntent, Strategy

DAILY = "1d"


class SwingStrategy(Strategy):
    """일봉만 본다. 수급도 지표도 보지 않는다.

    진입 — 둘 다 만족할 때
      1. 오늘 종가가 직전 `breakout_days` 일 최고가를 넘는다
      2. 종가가 `ma_long` 이평 위에 있다

    청산 — 하나라도 걸리면
      1. 진입가 대비 `stop_loss` 만큼 빠진다
      2. 종가가 `ma_exit` 이평을 밑돈다

    **조건 하나가 곧 과최적화할 손잡이 하나다.** 실질 개발 구간이 1년 반뿐이라
    조건을 늘리면 그 구간에만 맞는 규칙이 된다. 추적 손절을 따로 두지 않은 것도
    같은 이유다. 이평 이탈이 이미 그 역할을 한다.

    국면은 보지 않는다. `RiskManager` 가 이미 국면별로 금액을 줄인다.
    """

    name = "swing"

    # ------------------------------------------------------------------
    # **실전에 쓰지 않는다.** 개발 구간(2023-08~2025-08) 측정 결과 매매당
    # 기대값이 -1.60% 다. 총수익률 -34.38%, MDD 54%.
    #
    # 진입 신호에 우위는 있다 (돌파 20일 +0.30% vs 모집단 -0.67%). 다만
    # 왕복 비용 0.43% 보다 작아 남는 것이 없다.
    #
    # Phase 7 은 미완으로 멈췄다. 재개 지점은 checklist.md 와
    # context-notes.md 2026-08-30 (19) 에 있다.
    # ------------------------------------------------------------------

    def scan(self, ctx: Context) -> list[EntryIntent]:
        need = max(ctx.params["ma_long"], ctx.params["breakout_days"] + 1)
        intents = []

        for stock_id in ctx.feed.get_universe():
            if stock_id in ctx.positions:
                continue

            candles = ctx.feed.get_candles(stock_id, DAILY, need)
            breakout = self._breakout(candles, ctx.params)
            if breakout is None:
                continue

            intents.append(
                EntryIntent(
                    stock_id=stock_id,
                    side=Side.BUY,
                    strength=breakout,
                    payload={"reason": "breakout", "excess": str(breakout)},
                )
            )
            if len(intents) >= ctx.params["entries_per_day"]:
                break

        return intents

    def manage(self, ctx: Context, position: Position) -> ExitIntent | None:
        """손절을 먼저 본다. 둘 다 걸리면 더 구체적인 사유를 남긴다."""
        if self._hit_stop(ctx, position):
            return ExitIntent(position.stock_id, position.quantity, "stop")

        if self._lost_trend(ctx, position.stock_id):
            return ExitIntent(position.stock_id, position.quantity, "signal")

        return None

    # --- 진입 --------------------------------------------------------------

    def _breakout(self, candles: list[Candle], params: dict) -> Decimal | None:
        """돌파 폭을 낸다. 조건을 못 채우면 `None`.

        **조정가로 판단한다.** 분할일이 급락으로 보이면 추세가 끊긴다 (3.3).

        돌파 폭은 직전 최고가 대비 초과 비율이다. 이미 읽은 데이터에서 나오므로
        새 파라미터가 필요 없다. 지금은 `RiskManager` 가 쓰지 않지만 나중에
        순위를 매길 자리가 된다.
        """
        window = params["breakout_days"]
        if len(candles) < max(params["ma_long"], window + 1):
            # 예열이 덜 됐다. 없는 이평을 지어내지 않는다
            return None

        close = candles[-1].close
        prior_high = max(candle.high for candle in candles[-(window + 1) : -1])
        if close <= prior_high:
            return None

        if close <= _mean(candles, params["ma_long"]):
            return None

        return (close / prior_high - Decimal(1)) * 100

    # --- 청산 --------------------------------------------------------------

    def _hit_stop(self, ctx: Context, position: Position) -> bool:
        """**원주가끼리 견준다.** `avg_price` 가 체결가 기반이라 원주가다.

        조정가와 섞으면 조정계수만큼 어긋난 손절이 나간다 (3.3).
        """
        price = ctx.feed.get_quote(position.stock_id).price
        loss = (position.avg_price - price) / position.avg_price
        return loss >= Decimal(str(ctx.params["stop_loss"]))

    def _lost_trend(self, ctx: Context, stock_id: str) -> bool:
        """**조정가끼리 견준다.** 이평은 이어진 시계열이라야 뜻이 있다."""
        window = ctx.params["ma_exit"]
        candles = ctx.feed.get_candles(stock_id, DAILY, window)
        if len(candles) < window:
            return False

        return candles[-1].close < _mean(candles, window)


def _mean(candles: list[Candle], window: int) -> Decimal:
    """마지막 `window` 개 종가의 단순평균."""
    closes = [candle.close for candle in candles[-window:]]
    return sum(closes, Decimal(0)) / len(closes)
