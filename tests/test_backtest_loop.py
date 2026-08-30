# 하루 단위 루프. 더미 전략으로 완주하는지와 미래 참조가 막히는지 본다

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from backtest.execution import Executor
from backtest.loop import BacktestLoop, Order
from backtest.portfolio import Portfolio
from common.config import load_config
from common.risk import RiskManager
from common.strategy.dummy import DummyStrategy
from common.types import Candle, Quote, Regime, Side

STOCKS = [f"KRX:00000{n}" for n in range(1, 5)]
DAYS = [date(2025, 3, 3) + timedelta(days=n) for n in range(5)]

LIMITS = load_config("limits")
COSTS = LIMITS["backtest"]
NO_COST = {"fee_rate": 0, "slippage_rate": 0, "tax_rate": {"KOSPI": 0}}


class FakeMarket:
    """가격이 매일 같은 시장. 값이 아니라 순서를 시험한다."""

    def __init__(self, opens=None, closes=None, delisted=None):
        self.opens = opens or {}
        self.closes = closes or {}
        self.delisted = delisted or {}

    def trading_days(self, start, end):
        return [day for day in DAYS if start <= day <= end]

    def open_on(self, stock_id, day):
        if day not in DAYS or self.delisted.get(stock_id, date.max) <= day:
            return None
        return self.opens.get((stock_id, day), Decimal(10000))

    def last_close(self, stock_id, day):
        traded = [d for d in DAYS if d <= day]
        if not traded:
            return None
        last = min(traded[-1], self.delisted.get(stock_id, date.max) - timedelta(1))
        return (last, self.closes.get((stock_id, last), Decimal(10000)))

    def board_at(self, stock_id, day):
        return "KOSPI"

    def delisted_at(self, stock_id):
        return self.delisted.get(stock_id)


class FakeFeed:
    """커서를 옮길 수 있는 최소 피드. 커서 이후는 보여주지 않는다."""

    def __init__(self, day=DAYS[0]):
        self.day = day
        self.candle_days: list[date] = []

    def set_date(self, day):
        self.day = day

    def now(self):
        return datetime.combine(self.day, datetime.min.time(), tzinfo=UTC)

    def get_candles(self, stock_id, interval, count):
        self.candle_days = [d for d in DAYS if d <= self.day][-count:]
        return [
            Candle(
                stock_id,
                datetime.combine(d, datetime.min.time(), tzinfo=UTC),
                *(Decimal(10000),) * 4,
                0,
            )
            for d in self.candle_days
        ]

    def get_quote(self, stock_id):
        return Quote(stock_id, self.now(), Decimal(10000), None, None, 0)

    def get_universe(self):
        return list(STOCKS)

    def get_regime(self):
        return Regime.NEUTRAL

    def get_signals(self, strategy, since):
        return []


def make_loop(market=None, capital=Decimal(10_000_000), costs=COSTS, feed=None):
    return BacktestLoop(
        feed=feed or FakeFeed(),
        market=market or FakeMarket(),
        strategy=DummyStrategy(),
        risk=RiskManager(LIMITS["risk"]),
        executor=Executor(costs),
        portfolio=Portfolio(account_id="dummy", cash=capital),
        params=load_config("strategy_dummy"),
    )


def test_dummy_strategy_completes_a_run():
    """더미 전략으로 백테스트가 완주한다 (Phase 6 완료 기준)."""
    result = make_loop().run(DAYS)

    assert len(result.equity_curve) == len(DAYS)
    assert result.executions
    assert all(day in DAYS for day, _ in result.equity_curve)


def test_nothing_fills_on_the_first_day():
    """첫날은 전일 종가에 정한 주문이 없다. 체결은 이틀째부터다."""
    result = make_loop().run(DAYS)

    assert min(ex.fill.day for ex in result.executions) == DAYS[1]


def test_signal_day_and_fill_day_are_different():
    """종가로 정하고 다음 거래일 시가에 체결한다. 미래 참조가 구조적으로 막힌다."""
    result = make_loop().run(DAYS[:2])

    # 첫날 종가에 정한 매수가 둘째 날 시가에 체결된다
    assert all(ex.fill.day == DAYS[1] for ex in result.executions)
    assert all(ex.fill.side is Side.BUY for ex in result.executions)


def test_feed_never_looks_past_the_cursor():
    """피드가 커서 이후 데이터를 내지 않는다 (CLAUDE.md 필수 테스트)."""
    feed = FakeFeed()
    loop = make_loop(feed=feed)
    loop.run(DAYS[:3])

    feed.get_candles(STOCKS[0], "1d", 100)
    assert max(feed.candle_days) <= DAYS[2]


def test_costs_change_the_result():
    """수수료·슬리피지 변경이 결과에 반영된다 (Phase 6 완료 기준)."""
    real = make_loop().run(DAYS)
    free = make_loop(costs=NO_COST).run(DAYS)

    assert real.final_capital < free.final_capital


def test_round_trip_only_loses_money_when_price_is_flat():
    """가격이 그대로면 왕복 비용만큼 자산이 준다."""
    result = make_loop().run(DAYS)
    assert result.final_capital < result.initial_capital


def test_sells_settle_before_buys():
    """매도가 먼저 체결돼야 그 대금으로 당일 매수가 된다.

    하루의 한 단계만 떼어 본다. 더미 전략은 사는 날과 파는 날이 갈려서
    같은 날 매도·매수가 겹치지 않는다.
    """
    market = FakeMarket(opens={(STOCKS[0], DAYS[1]): Decimal(20000)})
    loop = make_loop(market=market, capital=Decimal(0))
    loop.portfolio.apply(loop.executor.buy(STOCKS[0], DAYS[0], Decimal(10000), 1))
    loop.portfolio.cash = Decimal(0)  # 현금이 없다. 매도대금이 들어와야 산다

    # **일부러 매수를 앞에 둔다.** 순서를 지키지 않으면 매수가 취소된다
    loop.pending = [
        Order(STOCKS[1], Side.BUY, 1, "entry"),
        Order(STOCKS[0], Side.SELL, 1, "timeout"),
    ]
    loop._settle(DAYS[1])

    assert STOCKS[1] in loop.portfolio.positions
    assert STOCKS[0] not in loop.portfolio.positions


def test_cash_never_goes_negative():
    """갭상승에 현금이 음수가 되면 없는 레버리지가 결과를 좋게 만든다."""
    gap = {(stock, day): Decimal(30000) for stock in STOCKS for day in DAYS}
    loop = make_loop(market=FakeMarket(opens=gap), capital=Decimal(100_000))
    loop.run(DAYS)

    assert loop.portfolio.cash >= 0


def test_order_is_cancelled_when_the_stock_does_not_trade():
    """그날 시가가 없으면 주문을 취소한다. 다음 날로 미루지 않는다."""
    market = FakeMarket(delisted={STOCKS[0]: DAYS[1]})
    loop = make_loop(market=market)
    result = loop.run(DAYS)

    assert not any(
        ex.fill.stock_id == STOCKS[0]
        and ex.fill.side is Side.BUY
        and ex.fill.day >= DAYS[1]
        for ex in result.executions
    )


def test_delisted_position_is_liquidated_at_the_last_price():
    """보유 중 폐지되면 정리매매 마지막 가격으로 청산한다 (ROADMAP.md Phase 6)."""
    # 이틀째 시가에 사고, 사흘째에 폐지된다
    market = FakeMarket(
        delisted={STOCKS[0]: DAYS[2]},
        closes={(STOCKS[0], DAYS[1]): Decimal(8000)},
    )
    loop = make_loop(market=market)
    result = loop.run(DAYS)

    liquidation = [
        ex
        for ex in result.executions
        if ex.fill.stock_id == STOCKS[0] and ex.fill.side is Side.SELL
    ]
    assert liquidation, "폐지 종목이 청산되지 않았다"
    assert liquidation[0].fill.day == DAYS[1]  # 마지막 거래일
    assert liquidation[0].reason == "delisted"
    assert STOCKS[0] not in loop.portfolio.positions


def test_position_limit_is_enforced_by_risk_not_strategy():
    """한도는 RiskManager 가 강제한다. 전략은 수량을 모른다."""
    loop = make_loop()
    loop.run(DAYS)

    assert len(loop.portfolio.positions) <= LIMITS["risk"]["max_positions"]


def test_execution_keeps_the_reason_and_the_payload():
    """체결만 남기면 청산 사유가 사라진다. backtest_trade 가 그것을 요구한다."""
    result = make_loop().run(DAYS)

    assert {ex.reason for ex in result.executions} == {"entry", "timeout"}
    assert all(
        ex.payload == {"reason": "dummy"}
        for ex in result.executions
        if ex.reason == "entry"
    )
