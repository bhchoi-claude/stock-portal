# 체결을 매매로 짝짓고 지표를 계산한다 (SCHEMA.md 7장)

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from common.types import Side

from .loop import Execution

SEOUL = ZoneInfo("Asia/Seoul")

# 지표는 소수 넷째 자리까지다. backtest_run 컬럼이 NUMERIC(10,4) 다
PLACES = Decimal("0.0001")


@dataclass(frozen=True)
class Trade:
    """`backtest_trade` 한 행. 매수 한 묶음과 그것을 판 매도의 짝이다.

    `exit_at` 이 `None` 이면 구간이 끝날 때까지 들고 있던 것이다. 지우지 않고
    남긴다. 미청산 포지션을 빼면 손익이 실제와 달라진다.
    """

    stock_id: str
    entry_at: datetime
    entry_price: Decimal
    quantity: int
    exit_at: datetime | None = None
    exit_price: Decimal | None = None
    pnl: Decimal | None = None
    pnl_rate: Decimal | None = None
    exit_reason: str | None = None
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class Metrics:
    total_return: Decimal
    mdd: Decimal
    win_rate: Decimal | None
    trade_count: int
    sharpe: Decimal | None


@dataclass
class _Lot:
    """아직 팔지 않은 매수 묶음. FIFO 로 소진된다."""

    day: date
    price: Decimal  # 체결 단가
    unit_cost: Decimal  # 수수료를 포함한 주당 원가
    quantity: int
    payload: dict[str, Any] | None


def pair_trades(executions: list[Execution], open_time: time) -> list[Trade]:
    """체결을 **FIFO 로** 짝짓는다 (2026-08-30 승인).

    분할 매수·부분 청산이 있으면 어느 매수를 판 것인지 정할 방법이 없다.
    먼저 산 것을 먼저 판 것으로 본다. 세법과도 같은 규칙이다.

    손익은 **현금 증감으로 계산한다.** 매수 원가에는 수수료가, 매도 대금에는
    수수료와 세금이 이미 반영돼 있다. `entry_price` 와 `exit_price` 는 체결
    단가라서 그 둘의 차이에 수량을 곱해도 `pnl` 이 나오지 않는다. 그 차이가
    거래비용이다.
    """
    lots: dict[str, deque[_Lot]] = defaultdict(deque)
    trades: list[Trade] = []

    for execution in executions:
        fill = execution.fill
        if fill.side is Side.BUY:
            lots[fill.stock_id].append(
                _Lot(
                    day=fill.day,
                    price=fill.price,
                    unit_cost=(fill.gross + fill.fee) / fill.quantity,
                    quantity=fill.quantity,
                    payload=execution.payload,
                )
            )
            continue

        trades.extend(_close(lots[fill.stock_id], execution, open_time))

    trades.extend(_open_trades(lots, open_time))
    return sorted(trades, key=lambda t: (t.entry_at, t.stock_id))


def compute(
    initial_capital: Decimal,
    equity_curve: list[tuple[date, Decimal]],
    trades: list[Trade],
    *,
    days_per_year: int,
) -> Metrics:
    """지표 다섯. 계산할 수 없으면 0 이 아니라 `None` 이다.

    거래가 없는데 승률 0% 를 적으면 '다 졌다' 로 읽힌다. 없는 것과 나쁜 것은
    다르다.
    """
    final = equity_curve[-1][1] if equity_curve else initial_capital
    closed = [t for t in trades if t.pnl is not None]

    return Metrics(
        total_return=_round((final - initial_capital) / initial_capital),
        mdd=_round(_mdd(equity_curve)),
        win_rate=(
            _round(Decimal(sum(t.pnl > 0 for t in closed)) / len(closed))
            if closed
            else None
        ),
        trade_count=len(trades),
        sharpe=_sharpe(equity_curve, days_per_year),
    )


def survivorship_note(delisted: int, with_prices: int, from_date: date) -> str:
    """생존편향 경고 (2026-08-30 승인).

    `PROJECT.md` 11장이 이미 경고하지만 문서에만 적어두면 몇 달 뒤 숫자만
    떼어 볼 때 잊는다. **결과 옆에 붙여둔다.**
    """
    return (
        f"생존편향 경고: 이 구간에 폐지된 종목 {delisted}개 중 "
        f"{with_prices}개만 일봉이 남아 있다. "
        f"일봉은 {from_date} 이후에도 살아남은 종목 위주라 "
        "폐지된 종목의 손실이 결과에 덜 반영돼 있다. "
        "편향은 없앨 수 없고 숫자를 그만큼 낮춰 봐야 한다."
    )


# --- 짝짓기 -------------------------------------------------------------


def _close(lots: deque[_Lot], execution: Execution, open_time: time) -> list[Trade]:
    """매도 하나를 오래된 매수부터 채운다."""
    fill = execution.fill
    # 수수료·세금을 뺀 주당 실수령액
    per_share = fill.cash / fill.quantity
    remaining = fill.quantity
    trades = []

    while remaining > 0 and lots:
        lot = lots[0]
        taken = min(lot.quantity, remaining)
        cost = lot.unit_cost * taken
        pnl = per_share * taken - cost

        trades.append(
            Trade(
                stock_id=fill.stock_id,
                entry_at=_at(lot.day, open_time),
                entry_price=lot.price,
                quantity=taken,
                exit_at=_at(fill.day, open_time),
                exit_price=fill.price,
                pnl=pnl,
                pnl_rate=_round(pnl / cost),
                exit_reason=execution.reason,
                payload=lot.payload,
            )
        )

        lot.quantity -= taken
        remaining -= taken
        if lot.quantity == 0:
            lots.popleft()

    return trades


def _open_trades(lots: dict[str, deque[_Lot]], open_time: time) -> list[Trade]:
    """구간이 끝날 때까지 들고 있던 것. 손익 칸은 비운다."""
    return [
        Trade(
            stock_id=stock_id,
            entry_at=_at(lot.day, open_time),
            entry_price=lot.price,
            quantity=lot.quantity,
            payload=lot.payload,
        )
        for stock_id, queue in lots.items()
        for lot in queue
    ]


# --- 지표 ---------------------------------------------------------------


def _mdd(curve: list[tuple[date, Decimal]]) -> Decimal:
    """최대낙폭. 고점 대비 가장 깊었던 하락이다. 양수로 낸다."""
    peak = Decimal(0)
    worst = Decimal(0)
    for _, equity in curve:
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak)
    return worst


def _sharpe(curve: list[tuple[date, Decimal]], days_per_year: int) -> Decimal | None:
    """샤프. **무위험수익률 0, 연 252거래일** (2026-08-30 승인).

    근거가 아니라 관례다. 값을 바꾸면 숫자가 달라지므로 `params` 에 남긴다.

    비율 계산이라 float 로 한다. `Decimal` 금지는 금액에 대한 규칙이다
    (CLAUDE.md 4). 표준편차가 0이거나 표본이 둘 미만이면 낼 수 없다.
    """
    returns = [
        float(curve[i][1] / curve[i - 1][1] - 1)
        for i in range(1, len(curve))
        if curve[i - 1][1] > 0
    ]
    if len(returns) < 2:
        return None

    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    if variance <= 0:
        return None

    return _round(Decimal(str(mean / math.sqrt(variance) * math.sqrt(days_per_year))))


def _round(value: Decimal) -> Decimal:
    return value.quantize(PLACES)


def _at(day: date, open_time: time) -> datetime:
    """체결 시각. **시가 체결이므로 장 시작 시각이다** (CLAUDE.md 5, UTC 저장).

    폐지 청산만은 정리매매 마지막 **종가**로 체결되는데 시각은 여기서도
    장 시작으로 찍힌다. 거래일은 맞고 시분은 근사다.
    """
    return datetime.combine(day, open_time, tzinfo=SEOUL).astimezone(UTC)
