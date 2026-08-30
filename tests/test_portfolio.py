# 백테스트 장부. 평단가에 수수료가 들어가는지 확인한다

from datetime import date
from decimal import Decimal

from backtest.execution import Executor
from backtest.portfolio import Portfolio

DAY = date(2025, 3, 3)
STOCK = "KRX:005930"
PARAMS = {
    "fee_rate": 0.00015,
    "slippage_rate": 0.001,
    "tax_rate": {"KOSPI": 0.0020},
}


def make() -> tuple[Portfolio, Executor]:
    return Portfolio(account_id="dummy", cash=Decimal(1_000_000)), Executor(PARAMS)


def test_average_price_includes_the_fee():
    """현금이 준 만큼이 원가다. 수수료를 빼면 손익이 낙관 쪽으로 틀어진다."""
    portfolio, executor = make()
    portfolio.apply(executor.buy(STOCK, DAY, Decimal(10000), 10))

    # 10000 × 1.001 × 10 = 100,100 + 수수료 15 = 100,115
    assert portfolio.positions[STOCK].avg_price == Decimal("10011.5")
    assert portfolio.cash == Decimal(899_885)


def test_partial_sell_keeps_the_average_price():
    portfolio, executor = make()
    portfolio.apply(executor.buy(STOCK, DAY, Decimal(10000), 10))
    before = portfolio.positions[STOCK].avg_price
    portfolio.apply(executor.sell(STOCK, DAY, Decimal(10000), 4, "KOSPI"))

    assert portfolio.positions[STOCK].quantity == 6
    assert portfolio.positions[STOCK].avg_price == before


def test_full_sell_removes_the_position():
    portfolio, executor = make()
    portfolio.apply(executor.buy(STOCK, DAY, Decimal(10000), 10))
    portfolio.apply(executor.sell(STOCK, DAY, Decimal(10000), 10, "KOSPI"))

    assert STOCK not in portfolio.positions
    assert portfolio.cash < Decimal(1_000_000)  # 왕복 비용만 남는다


def test_equity_falls_back_to_the_average_price():
    """값이 없는 종목은 평단가로 본다. 없는 가격을 지어내지 않는다."""
    portfolio, executor = make()
    portfolio.apply(executor.buy(STOCK, DAY, Decimal(10000), 10))

    assert portfolio.equity({}) == portfolio.cash + Decimal(100_115)
    assert portfolio.equity({STOCK: Decimal(20000)}) == portfolio.cash + Decimal(
        200_000
    )
