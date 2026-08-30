# 하루 단위 백테스트 루프. 조각들을 잇는 자리다 (ROADMAP.md Phase 6)

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from common.feed.backtest import BacktestFeed
from common.risk import RiskManager
from common.strategy.base import Context, Strategy
from common.types import Balance, Side

from .execution import Executor, Fill
from .market import Market
from .portfolio import Portfolio

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Order:
    """다음 거래일 시가에 낼 주문. 종가에 정해지고 다음 날 체결된다."""

    stock_id: str
    side: Side
    quantity: int
    reason: str  # 진입은 'entry', 청산은 ExitIntent.reason
    payload: dict[str, Any] | None = None  # 진입 근거. backtest_trade 에 남는다


@dataclass(frozen=True)
class Execution:
    """체결과 **왜 그랬는지**. 체결만 남기면 청산 사유가 사라진다.

    `Fill` 에 넣지 않은 것은 체결 시뮬레이터가 이유를 모르기 때문이다.
    수수료 계산에 사유는 필요 없다. 아는 쪽인 루프가 붙인다.
    """

    fill: Fill
    reason: str
    payload: dict[str, Any] | None = None


@dataclass
class BacktestResult:
    initial_capital: Decimal
    final_capital: Decimal
    equity_curve: list[tuple[date, Decimal]] = field(default_factory=list)
    executions: list[Execution] = field(default_factory=list)


class BacktestLoop:
    """**신호는 종가, 체결은 다음 시가.** 그 사이에 커서가 하루 넘어간다.

    하루의 순서가 이 클래스의 전부다.

    1. 전일 종가에 정한 주문을 오늘 시가에 체결한다 (매도 먼저)
    2. 커서를 오늘 장 마감으로 옮긴다
    3. 보유 중 폐지된 종목을 정리매매 마지막 가격으로 청산한다
    4. 오늘 종가로 평가한다
    5. `manage` 로 청산을, `scan` 으로 진입을 정한다. 둘 다 매일 돈다

    미래 참조가 구조적으로 막히는 것은 2와 1의 순서 때문이다. 오늘 종가를
    보고 정한 것은 아무리 빨라도 내일 시가에야 체결된다.
    """

    def __init__(
        self,
        *,
        feed: BacktestFeed,
        market: Market,
        strategy: Strategy,
        risk: RiskManager,
        executor: Executor,
        portfolio: Portfolio,
        params: dict[str, Any],
    ) -> None:
        self.feed = feed
        self.market = market
        self.strategy = strategy
        self.risk = risk
        self.executor = executor
        self.portfolio = portfolio
        self.params = params
        self.pending: list[Order] = []
        self.executions: list[Execution] = []

    def run(self, days: list[date]) -> BacktestResult:
        initial = self.portfolio.cash
        equity = initial
        curve: list[tuple[date, Decimal]] = []
        started = False

        for day in days:
            # 일일 손실 한도의 기준은 **전일 종가 평가액**이다.
            # 당일 종가로 잡으면 손실이 늘 0이라 한도가 죽는다
            self.risk.start_day(equity)

            # 권리락은 그날 시가부터 적용된다. 체결보다 먼저 반영해야 한다
            self._adjust(day)
            self._settle(day)
            self.feed.set_date(day)
            self._liquidate_delisted(day)

            prices = self._closes(day)
            equity = self.portfolio.equity(prices)
            ctx = self._context(prices)

            if not started:
                self.strategy.on_start(ctx)
                started = True

            self.pending = self._plan(ctx)
            self.strategy.on_day_end(ctx)
            curve.append((day, equity))

        return BacktestResult(
            initial_capital=initial,
            final_capital=equity,
            equity_curve=curve,
            executions=self.executions,
        )

    # --- 하루의 각 단계 -------------------------------------------------

    def _settle(self, day: date) -> None:
        """전일 주문을 오늘 시가에 체결한다. **매도를 먼저 낸다.**

        매도대금으로 당일 매수가 가능한 것이 실제다. 순서를 뒤집으면
        현금이 있는데도 사지 못하는 날이 생긴다.
        """
        orders, self.pending = self.pending, []
        for order in sorted(orders, key=lambda o: o.side is Side.BUY):
            open_price = self.market.open_on(order.stock_id, day)
            if open_price is None:
                # 그날 거래가 없으면 체결될 수 없다. 다음 날로 미루지 않는다
                log.info("%s %s 시가가 없어 주문을 취소합니다", day, order.stock_id)
                continue

            fill = (
                self._buy(order, day, open_price)
                if order.side is Side.BUY
                else self._sell(order, day, open_price)
            )
            if fill is not None:
                self._record(fill, order.reason, order.payload)

    def _adjust(self, day: date) -> None:
        """권리락일에 보유 수량과 평단가를 맞춘다.

        `price_daily` 는 원주가라 이날부터 가격이 기계적으로 바뀐다. 수량을
        함께 바꾸지 않으면 평가액이 증발하고 손절이 헛발동한다.

        **대기 주문의 수량은 어제 정한 것이라 조정 전 수량이다.** 매도가
        모자라게 나가면 남은 만큼은 다음 주기에 `manage` 가 다시 판다.
        주문을 여기서 다시 계산하지 않는다.
        """
        for stock_id, ratio in self.market.adjustments(day):
            if stock_id not in self.portfolio.positions:
                continue
            log.info("%s %s 권리락 반영 (비율 %s)", day, stock_id, ratio)
            self.portfolio.adjust(stock_id, ratio)

    def _liquidate_delisted(self, day: date) -> None:
        """보유 중 폐지된 종목을 **정리매매 마지막 가격**으로 청산한다.

        폐지되면 시가가 더는 나오지 않아 `_settle` 이 영원히 취소한다.
        장부에 값 없는 포지션이 남지 않도록 여기서 털어낸다.
        """
        for stock_id, position in list(self.portfolio.positions.items()):
            delisted_at = self.market.delisted_at(stock_id)
            if delisted_at is None or delisted_at > day:
                continue

            last = self.market.last_close(stock_id, day)
            if last is None:
                log.warning("%s 의 마지막 가격이 없어 청산하지 못했습니다", stock_id)
                continue

            last_day, close = last
            fill = self.executor.sell(
                stock_id,
                last_day,
                close,
                position.quantity,
                self.market.board_at(stock_id, last_day) or "",
            )
            log.info("%s 폐지로 %s 에 청산합니다", stock_id, last_day)
            self._record(fill, "delisted")

    def _plan(self, ctx: Context) -> list[Order]:
        """오늘 종가로 내일 낼 주문을 정한다.

        `manage` 가 `scan` 보다 먼저다. 신규 진입을 막은 상태에서도 청산은
        돌아야 한다는 규격과 같은 순서다 (INTERFACES.md 4.1).
        """
        orders = [
            Order(intent.stock_id, Side.SELL, intent.quantity, intent.reason)
            for position in list(ctx.positions.values())
            if (intent := self.strategy.manage(ctx, position)) is not None
        ]

        regime = self.feed.get_regime()
        for intent in self.strategy.scan(ctx):
            decision = self.risk.evaluate(intent, ctx, regime)
            if not decision.approved:
                log.debug("%s 진입 거부: %s", intent.stock_id, decision.reason)
                continue
            orders.append(
                Order(
                    intent.stock_id,
                    intent.side,
                    decision.quantity,
                    "entry",
                    intent.payload,
                )
            )
        return orders

    # --- 보조 ------------------------------------------------------------

    def _buy(self, order: Order, day: date, open_price: Decimal) -> Fill | None:
        """현금이 모자라면 살 수 있는 만큼만 산다.

        갭상승으로 현금이 음수가 되면 있지도 않은 레버리지가 결과를 좋게
        만든다. 미수는 쓰지 않는다.
        """
        quantity = order.quantity
        while quantity > 0:
            fill = self.executor.buy(order.stock_id, day, open_price, quantity)
            if -fill.cash <= self.portfolio.cash:
                return fill
            # 부족분 비율만큼 줄인다. 반올림 때문에 한 주씩은 반드시 준다
            quantity = min(
                quantity - 1, int(quantity * self.portfolio.cash / -fill.cash)
            )

        log.info("%s %s 현금이 모자라 매수를 취소합니다", day, order.stock_id)
        return None

    def _sell(self, order: Order, day: date, open_price: Decimal) -> Fill | None:
        held = self.portfolio.positions.get(order.stock_id)
        if held is None:
            return None

        return self.executor.sell(
            order.stock_id,
            day,
            open_price,
            min(order.quantity, held.quantity),
            self.market.board_at(order.stock_id, day) or "",
        )

    def _closes(self, day: date) -> dict[str, Decimal]:
        """보유 종목의 오늘 종가. **원주가다.** 조정가는 수량과 단위가 다르다."""
        closes = {}
        for stock_id in self.portfolio.positions:
            last = self.market.last_close(stock_id, day)
            if last is not None:
                closes[stock_id] = last[1]
        return closes

    def _context(self, prices: dict[str, Decimal]) -> Context:
        eval_amount = self.portfolio.eval_amount(prices)
        cash = self.portfolio.cash
        return Context(
            feed=self.feed,
            account_id=self.portfolio.account_id,
            params=self.params,
            positions=dict(self.portfolio.positions),
            balance=Balance(
                account_id=self.portfolio.account_id,
                deposit=cash,
                available=cash,
                eval_amount=eval_amount,
                total_asset=cash + eval_amount,
            ),
        )

    def _record(
        self, fill: Fill, reason: str, payload: dict[str, Any] | None = None
    ) -> None:
        self.portfolio.apply(fill)
        self.executions.append(Execution(fill, reason, payload))
