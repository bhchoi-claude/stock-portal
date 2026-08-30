# 백테스트 CLI. python -m backtest run --strategy dummy --from ... --to ...

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, time
from decimal import Decimal

from common.config import load_config
from common.db.conn import connect, transaction
from common.feed.backtest import BacktestFeed
from common.risk import RiskManager
from common.strategy.base import Strategy
from common.strategy.dummy import DummyStrategy

from .execution import Executor
from .loop import BacktestLoop, BacktestResult
from .market import DbMarket
from .portfolio import Portfolio

log = logging.getLogger(__name__)

# 지금은 더미뿐이다. 실전 전략은 Phase 7 에서 붙인다
STRATEGIES: dict[str, type[Strategy]] = {"dummy": DummyStrategy}


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.to < args.from_date:
        print("--from 이 --to 보다 뒤입니다.")
        return 2

    limits = load_config("limits")
    settings = limits["backtest"]
    strategy = STRATEGIES[args.strategy]()
    params = load_config(f"strategy_{args.strategy}")

    capital = Decimal(str(args.capital or settings["initial_capital"]))

    with connect() as conn, transaction(conn) as cur:
        market = DbMarket(cur)
        days = market.trading_days(args.from_date, args.to)
        if not days:
            print("그 구간에 거래일이 없습니다. 일봉이 적재됐는지 확인하세요.")
            return 1

        loop = BacktestLoop(
            feed=BacktestFeed(
                cur,
                days[0],
                close_time=time.fromisoformat(settings["close_time"]),
                universe_size=settings["universe_size"],
            ),
            market=market,
            strategy=strategy,
            risk=RiskManager(limits["risk"]),
            executor=Executor(settings),
            portfolio=Portfolio(account_id=args.strategy, cash=capital),
            params=params,
        )
        result = loop.run(days)

    _report(args.strategy, days[0], days[-1], result)
    return 0


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m backtest")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="백테스트를 실행한다")
    run.add_argument("--strategy", required=True, choices=sorted(STRATEGIES))
    # `from` 은 예약어라 dest 를 따로 준다
    run.add_argument(
        "--from",
        dest="from_date",
        required=True,
        type=date.fromisoformat,
        help="시작 거래일",
    )
    run.add_argument("--to", required=True, type=date.fromisoformat, help="종료 거래일")
    run.add_argument(
        "--capital",
        type=int,
        help="초기 자본. 없으면 config/limits.yaml 의 값을 쓴다",
    )
    return parser.parse_args(argv)


def _report(strategy: str, first: date, last: date, result: BacktestResult) -> None:
    """지표는 아직 총수익률뿐이다. MDD·승률·샤프는 6단계에서 적재와 함께 넣는다."""
    profit = result.final_capital - result.initial_capital
    rate = profit / result.initial_capital * 100

    print(f"전략      {strategy}")
    print(f"구간      {first} ~ {last} ({len(result.equity_curve)} 거래일)")
    print(f"초기자본  {result.initial_capital:,.0f}")
    print(f"최종자본  {result.final_capital:,.0f}")
    print(f"총수익률  {rate:+.2f}%")
    print(f"체결      {len(result.fills)}건")


if __name__ == "__main__":
    sys.exit(main())
