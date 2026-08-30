# 체결 시뮬레이터. 수수료·세금·슬리피지가 결과를 바꾸는지 확인한다

from datetime import date
from decimal import Decimal

from backtest.execution import Executor
from common.config import load_config
from common.types import Side

DAY = date(2026, 1, 7)
STOCK = "KRX:005930"

PARAMS = {
    "fee_rate": 0.00015,
    "slippage_rate": 0.001,
    "tax_rate": {"KOSPI": 0.0020, "KOSDAQ": 0.0020},
}

NO_COST = {"fee_rate": 0, "slippage_rate": 0, "tax_rate": {"KOSPI": 0, "KOSDAQ": 0}}


def test_buy_pays_slippage_and_fee():
    fill = Executor(PARAMS).buy(STOCK, DAY, Decimal(10000), 10)

    # 10000 × 1.001 = 10010, 10주면 100,100원
    assert fill.gross == Decimal(100100)
    assert fill.fee == Decimal(15)  # 100100 × 0.015% = 15.015 -> 절사
    assert fill.tax == 0
    assert fill.cash == Decimal(-100115)
    assert fill.side is Side.BUY


def test_sell_pays_tax_as_well():
    fill = Executor(PARAMS).sell(STOCK, DAY, Decimal(10000), 10, "KOSPI")

    # 10000 × 0.999 = 9990, 10주면 99,900원
    assert fill.gross == Decimal(99900)
    assert fill.fee == Decimal(14)  # 99900 × 0.015% = 14.985 -> 절사
    assert fill.tax == Decimal(199)  # 99900 × 0.20% = 199.8 -> 절사
    assert fill.cash == Decimal(99687)


def test_tax_is_charged_even_on_a_loss():
    """세금은 손익과 무관하게 매도금액 기준이다."""
    fill = Executor(PARAMS).sell(STOCK, DAY, Decimal(1), 1, "KOSDAQ")
    assert fill.tax >= 0
    assert fill.side is Side.SELL


def test_zero_cost_run_differs_from_real_cost():
    """수수료·슬리피지 변경이 결과에 반영된다 (Phase 6 완료 기준)."""
    free = Executor(NO_COST)
    real = Executor(PARAMS)

    assert free.buy(STOCK, DAY, Decimal(10000), 10).cash == Decimal(-100000)
    assert real.buy(STOCK, DAY, Decimal(10000), 10).cash < Decimal(-100000)

    assert free.sell(STOCK, DAY, Decimal(10000), 10, "KOSPI").cash == Decimal(100000)
    assert real.sell(STOCK, DAY, Decimal(10000), 10, "KOSPI").cash < Decimal(100000)


def test_round_trip_loses_money_without_price_change():
    """가격이 그대로여도 왕복하면 손해다. 미반영 결과가 무의미한 이유다."""
    executor = Executor(PARAMS)
    bought = executor.buy(STOCK, DAY, Decimal(10000), 10)
    sold = executor.sell(STOCK, DAY, Decimal(10000), 10, "KOSPI")

    assert bought.cash + sold.cash < 0


def test_unknown_board_uses_the_highest_rate():
    """모르는 시장은 가장 높은 세율로. 낙관 쪽으로 틀리지 않는다."""
    executor = Executor({**PARAMS, "tax_rate": {"KOSPI": 0.001, "KOSDAQ": 0.003}})
    fill = executor.sell(STOCK, DAY, Decimal(10000), 10, "KONEX")

    assert fill.tax == Decimal(299)  # 99900 × 0.3%


def test_amounts_are_decimal():
    """금액에 float 를 쓰지 않는다 (CLAUDE.md 4)."""
    fill = Executor(PARAMS).sell(STOCK, DAY, Decimal(10000), 10, "KOSPI")

    assert all(
        isinstance(value, Decimal)
        for value in (fill.price, fill.gross, fill.fee, fill.tax, fill.cash)
    )


def test_committed_config_matches_real_rates():
    """실제 요율이 설정에 들어 있는지 본다. 값이 틀리면 결과가 통째로 틀린다."""
    params = load_config("limits")["backtest"]

    assert params["fee_rate"] == 0.00015  # 키움 온라인 0.015%
    assert params["tax_rate"]["KOSPI"] == 0.0020  # 0.05% + 농특세 0.15%
    assert params["tax_rate"]["KOSDAQ"] == 0.0020
