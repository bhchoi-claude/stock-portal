# 백테스트 CLI. python -m backtest run --strategy dummy --from ... --to ...

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, time
from decimal import Decimal
from typing import Any

from common.config import load_config
from common.db.backtest import delisted_between, insert_run, insert_trades
from common.db.conn import connect, transaction
from common.feed.backtest import BacktestFeed
from common.risk import RiskManager
from common.strategy.base import Strategy
from common.strategy.dummy import DummyStrategy
from common.strategy.swing import SwingStrategy

from .execution import Executor
from .loop import BacktestLoop, BacktestResult
from .market import DbMarket
from .metrics import Metrics, Trade, compute, pair_trades, survivorship_note
from .portfolio import Portfolio

log = logging.getLogger(__name__)

# 더미는 틀 검증용이다. 결과의 좋고 나쁨에 뜻이 없다
STRATEGIES: dict[str, type[Strategy]] = {
    "dummy": DummyStrategy,
    "swing": SwingStrategy,
}


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.to < args.from_date:
        print("--from 이 --to 보다 뒤입니다.")
        return 2

    limits = load_config("limits")
    settings = limits["backtest"]
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
            strategy=STRATEGIES[args.strategy](),
            risk=RiskManager(limits["risk"]),
            executor=Executor(settings),
            portfolio=Portfolio(account_id=args.strategy, cash=capital),
            params=params,
        )
        result = loop.run(days)

        trades = pair_trades(
            result.executions, time.fromisoformat(settings["open_time"])
        )
        metrics = compute(
            capital,
            result.equity_curve,
            trades,
            days_per_year=settings["days_per_year"],
        )
        note = survivorship_note(*delisted_between(cur, days[0], days[-1]))

        # 적재는 실행과 같은 트랜잭션이다. 갈라지면 근거 없는 지표가 남는다
        run_id = _save(
            cur,
            args.strategy,
            days,
            {"strategy": params, "risk": limits["risk"], "backtest": settings},
            capital,
            result,
            metrics,
            trades,
            note,
        )

    _report(run_id, args.strategy, days, capital, result, metrics, note)
    return 0


def _save(
    cur: Any,
    strategy: str,
    days: list[date],
    params: dict[str, Any],
    capital: Decimal,
    result: BacktestResult,
    metrics: Metrics,
    trades: list[Trade],
    note: str,
) -> int:
    run_id = insert_run(
        cur,
        strategy=strategy,
        from_date=days[0],
        to_date=days[-1],
        universe=(
            f"거래대금 상위 {params['backtest']['universe_size']}종목,"
            " 관리종목·거래정지 제외"
        ),
        params=params,
        initial_capital=capital,
        final_capital=result.final_capital,
        total_return=metrics.total_return,
        mdd=metrics.mdd,
        win_rate=metrics.win_rate,
        trade_count=metrics.trade_count,
        sharpe=metrics.sharpe,
        fee_rate=Decimal(str(params["backtest"]["fee_rate"])),
        slippage_rate=Decimal(str(params["backtest"]["slippage_rate"])),
        note=note,
    )
    insert_trades(cur, run_id, trades)
    return run_id


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


def _report(
    run_id: int,
    strategy: str,
    days: list[date],
    capital: Decimal,
    result: BacktestResult,
    metrics: Metrics,
    note: str,
) -> None:
    print(f"run_id    {run_id}")
    print(f"전략      {strategy}")
    print(f"구간      {days[0]} ~ {days[-1]} ({len(days)} 거래일)")
    print(f"초기자본  {capital:,.0f}")
    print(f"최종자본  {result.final_capital:,.0f}")
    print(f"총수익률  {metrics.total_return * 100:+.2f}%")
    print(f"MDD       {metrics.mdd * 100:.2f}%")
    print(f"승률      {_pct(metrics.win_rate)}")
    print(f"매매      {metrics.trade_count}건 (체결 {len(result.executions)}건)")
    print(f"샤프      {_num(metrics.sharpe)}")
    print()
    print(note)


def _pct(value: Decimal | None) -> str:
    """없는 것과 나쁜 것은 다르다. 0% 로 적지 않는다."""
    return f"{value * 100:.2f}%" if value is not None else "계산 불가"


def _num(value: Decimal | None) -> str:
    return f"{value:.2f}" if value is not None else "계산 불가"


if __name__ == "__main__":
    sys.exit(main())
