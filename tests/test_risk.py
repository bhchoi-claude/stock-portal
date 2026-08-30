# RiskManager. 한도 초과 시 거부하는지 확인한다 (CLAUDE.md 필수 테스트)

from decimal import Decimal

import pytest
from test_strategy import StubFeed

from common.risk import RiskManager
from common.strategy.base import Context, EntryIntent
from common.types import Balance, Position, Regime, Side

STOCK = "KRX:000001"

PARAMS = {
    "regime_allocation": {"danger": 0.3, "neutral": 0.7, "safe": 1.0},
    "max_position_size": 1000000,
    "max_weight_per_stock": 0.1,
    "max_positions": 10,
    "daily_loss_limit": 0.03,
}

# StubFeed 의 현재가가 1000원이다
PRICE = Decimal(1000)


def make_context(total: int = 10000000, available: int | None = None, **overrides):
    base = {
        "feed": StubFeed(),
        "account_id": "swing",
        "params": {},
        "positions": {},
        "balance": Balance(
            "swing",
            Decimal(total),
            Decimal(total if available is None else available),
            Decimal(0),
            Decimal(total),
        ),
    }
    return Context(**{**base, **overrides})


def intent() -> EntryIntent:
    return EntryIntent(stock_id=STOCK, side=Side.BUY, strength=Decimal(50))


def test_position_count_limit_rejects():
    positions = {
        f"KRX:00000{n}": Position("swing", f"KRX:00000{n}", 1, PRICE) for n in range(10)
    }
    decision = RiskManager(PARAMS).evaluate(
        intent(), make_context(positions=positions), Regime.SAFE
    )

    assert not decision.approved
    assert decision.reason == "max_positions"


def test_daily_loss_limit_stops_new_entries():
    """전략이 뭘 하든 그날은 더 사지 않는다. 전략 밖에서 강제한다."""
    manager = RiskManager(PARAMS)
    manager.start_day(Decimal(10000000))

    decision = manager.evaluate(intent(), make_context(total=9700000), Regime.SAFE)

    assert not decision.approved
    assert decision.reason == "daily_loss_limit"


def test_loss_below_the_limit_still_trades():
    manager = RiskManager(PARAMS)
    manager.start_day(Decimal(10000000))

    assert manager.evaluate(intent(), make_context(total=9800000), Regime.SAFE).approved


def test_weight_cap_binds_before_regime_allocation():
    """한도 넷 중 가장 작은 것이 이긴다.

    총자산 500만이면 종목당 비중 10%(50만)가 위험 배분 30%(150만)보다 작다.
    그래서 국면이 달라도 첫 한 건의 수량은 같다.
    """
    manager = RiskManager(PARAMS)
    context = make_context(total=5000000)

    safe = manager.evaluate(intent(), context, Regime.SAFE)
    danger = manager.evaluate(intent(), context, Regime.DANGER)

    assert safe.quantity == 500
    assert danger.quantity == 500


def test_regime_allocation_counts_what_is_already_held():
    """배분을 빼지 않으면 위험 국면에서도 종목 수만큼 계속 살 수 있다."""
    held = {STOCK: Position("swing", STOCK, 2500, PRICE)}  # 250만 투입 중
    decision = RiskManager(PARAMS).evaluate(
        intent(), make_context(total=10000000, positions=held), Regime.DANGER
    )

    # 위험 배분 30% = 300만. 이미 250만 들었으니 50만 남는다
    assert decision.quantity == 500


def test_no_budget_left_is_rejected():
    held = {STOCK: Position("swing", STOCK, 3000, PRICE)}  # 300만
    decision = RiskManager(PARAMS).evaluate(
        intent(), make_context(total=10000000, positions=held), Regime.DANGER
    )

    assert not decision.approved
    assert decision.reason == "no_budget"


def test_available_cash_caps_the_order():
    decision = RiskManager(PARAMS).evaluate(
        intent(), make_context(total=10000000, available=120000), Regime.SAFE
    )

    assert decision.quantity == 120


def test_too_small_to_buy_one_share():
    decision = RiskManager(PARAMS).evaluate(
        intent(), make_context(total=10000000, available=500), Regime.SAFE
    )

    assert not decision.approved
    assert decision.reason == "too_small"


def test_limit_price_is_used_when_given():
    decision = RiskManager(PARAMS).evaluate(
        EntryIntent(STOCK, Side.BUY, Decimal(50), limit_price=Decimal(2000)),
        make_context(total=10000000, available=1000000),
        Regime.SAFE,
    )

    assert decision.quantity == 500


@pytest.mark.parametrize("regime", list(Regime))
def test_every_regime_has_an_allocation(regime):
    """국면이 늘어나면 설정도 함께 늘어야 한다. 빠지면 여기서 걸린다."""
    assert regime.value in PARAMS["regime_allocation"]
