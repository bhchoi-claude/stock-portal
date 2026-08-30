# Strategy 규격. 전략이 주문이 아니라 의도만 내는지 확인한다

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from common.config import load_config
from common.strategy.base import Context, EntryIntent, ExitIntent, Strategy
from common.strategy.dummy import DummyStrategy
from common.types import Balance, Candle, Position, Quote, Regime, Side

UNIVERSE = [f"KRX:00000{n}" for n in range(1, 9)]


class StubFeed:
    """규격만 만족하는 최소 피드. DB 없이 전략을 시험한다."""

    def now(self) -> datetime:
        return datetime(2026, 1, 7, 6, 30, tzinfo=UTC)

    def get_candles(self, stock_id: str, interval: str, count: int) -> list[Candle]:
        return []

    def get_quote(self, stock_id: str) -> Quote:
        return Quote(stock_id, self.now(), Decimal(1000), None, None, 0)

    def get_universe(self) -> list[str]:
        return list(UNIVERSE)

    def get_regime(self) -> Regime:
        return Regime.NEUTRAL

    def get_signals(self, strategy: str, since: datetime) -> list:
        return []


def make_context(**overrides) -> Context:
    base = {
        "feed": StubFeed(),
        "account_id": "swing",
        "params": load_config("strategy_dummy"),
        "positions": {},
        "balance": Balance("swing", Decimal(0), Decimal(0), Decimal(0), Decimal(0)),
    }
    return Context(**{**base, **overrides})


def test_scan_returns_intents_without_quantity():
    """전략은 수량을 정하지 않는다. 포지션 크기는 RiskManager 의 몫이다."""
    intents = DummyStrategy().scan(make_context())

    assert len(intents) == load_config("strategy_dummy")["entries_per_day"]
    assert all(isinstance(intent, EntryIntent) for intent in intents)
    assert not any(hasattr(intent, "quantity") for intent in intents)


def test_scan_skips_held_stocks():
    held = {UNIVERSE[0]: Position("swing", UNIVERSE[0], 10, Decimal(1000))}
    intents = DummyStrategy().scan(make_context(positions=held))

    assert UNIVERSE[0] not in [intent.stock_id for intent in intents]


def test_manage_exits_the_whole_position():
    position = Position("swing", UNIVERSE[0], 7, Decimal(1000))
    intent = DummyStrategy().manage(make_context(), position)

    assert isinstance(intent, ExitIntent)
    assert intent.quantity == 7
    assert intent.reason == "timeout"


def test_entry_intent_side_is_typed():
    intents = DummyStrategy().scan(make_context())
    assert all(intent.side is Side.BUY for intent in intents)


def test_strategy_cannot_be_instantiated_without_the_two_methods():
    """scan 과 manage 는 반드시 구현해야 한다."""

    class Half(Strategy):
        name = "half"

        def scan(self, ctx):
            return []

    with pytest.raises(TypeError):
        Half()


def test_hooks_are_optional():
    """on_start 와 on_day_end 는 기본 구현이 있다."""
    strategy = DummyStrategy()
    ctx = make_context()

    assert strategy.on_start(ctx) is None
    assert strategy.on_day_end(ctx) is None
