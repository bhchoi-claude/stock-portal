# 매매 상태를 한 통으로 묶어 텔레그램으로 보내는 CLI. 엔진 프로세스와 별개다

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from common.config import load_config
from common.db.conn import connect, transaction
from common.db.events import EventRow, recent_events
from common.db.heartbeat import ProcessState, list_heartbeats
from common.db.orders import OrderView, recent_orders
from common.db.pnl import PnlRow, recent_pnl
from common.db.positions import PositionView, position_views
from common.db.regime import RegimeRow, current_regime
from common.db.signals import SignalView, open_signals
from common.notify.base import Level, Notifier
from common.notify.telegram import TelegramNotifier

logger = logging.getLogger(__name__)

SEOUL = ZoneInfo("Asia/Seoul")

# 텔레그램 한 통 상한이 4096 자다. 제목 자리를 빼고 남긴다.
# 조절값이 아니라 프로토콜 한계라 설정에 두지 않는다
MAX_BODY = 3800


@dataclass(frozen=True)
class Report:
    """보고할 것. DB 에서 읽은 것만 담는다."""

    day: date
    engine: ProcessState | None
    stale_sec: int
    orders: list[OrderView]
    positions: list[PositionView]
    pending: list[SignalView]
    regime: RegimeRow | None
    pnl: PnlRow | None
    problems: list[EventRow]


def stale_seconds(portal: dict[str, Any], process_name: str) -> int:
    """화면이 '멈췄다' 고 보는 시간. 보고도 같은 기준을 쓴다.

    두 곳이 다른 값을 쓰면 화면은 빨간데 보고는 정상이라고 하는 일이 생긴다.
    """
    for entry in portal["processes"]:
        if entry["name"] == process_name:
            return entry["stale_after_minutes"] * 60
    return portal["default_stale_after_minutes"] * 60


def gather(cur, day: date, swing: dict[str, Any], portal: dict[str, Any]) -> Report:
    """그날 상태를 모은다. **엔진을 부르지 않는다** — DB 로만 본다 (CLAUDE.md 8)."""
    account_id, strategy = swing["account_id"], swing["strategy"]
    process_name = swing["process_name"]
    trading = portal["trading"]

    engine = next(
        (h for h in list_heartbeats(cur) if h.process_name == process_name), None
    )
    pnl = recent_pnl(cur, account_id, 1)
    return Report(
        day=day,
        engine=engine,
        stale_sec=stale_seconds(portal, process_name),
        orders=_today(recent_orders(cur, account_id, trading["orders_limit"]), day),
        positions=position_views(cur, account_id),
        pending=open_signals(cur, strategy, trading["signals_limit"]),
        regime=current_regime(cur),
        pnl=pnl[0] if pnl else None,
        problems=_today(
            recent_events(
                cur,
                levels=["WARN", "ERROR", "CRITICAL"],
                limit=portal["events_limit_default"],
            ),
            day,
        ),
    )


def render(report: Report) -> str:
    """평문으로 만든다. 종목명의 기호가 서식과 부딪히면 발송이 400 이 된다."""
    lines = [f"{report.day} ({'월화수목금토일'[report.day.weekday()]})", ""]

    lines.append(f"엔진 {_engine(report.engine, report.stale_sec)}")
    if report.regime:
        lines.append(f"국면 {report.regime.regime} ({report.regime.score:+.3f})")

    lines += ["", f"[ 오늘 주문 {len(report.orders)}건 ]"]
    lines += [_order(order) for order in report.orders] or ["- 없음"]

    lines += ["", f"[ 보유 {len(report.positions)}종목 ]"]
    lines += [_position(p) for p in report.positions] or ["- 없음"]

    if report.pending:
        lines += ["", f"[ 대기 중인 계획 {len(report.pending)}건 ]"]
        lines += [f"- {s.side} {s.name or s.stock_id}" for s in report.pending]

    if report.pnl:
        lines += ["", f"[ 손익 {report.pnl.trade_date} ]", _pnl(report.pnl)]

    if report.problems:
        lines += ["", f"[ 경고·오류 {len(report.problems)}건 ]"]
        lines += [
            f"- {e.created_at.astimezone(SEOUL):%H:%M} {e.process_name} {e.message}"
            for e in report.problems
        ]

    return "\n".join(lines)[:MAX_BODY]


def level(report: Report) -> Level:
    """엔진이 멈췄거나 오류가 있으면 눈에 띄게 보낸다."""
    if report.problems or report.engine is None:
        return "WARN"
    return "WARN" if report.engine.age_seconds > report.stale_sec else "INFO"


def main(argv: list[str], notifier: Notifier | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    swing = load_config("engine")["swing"]
    portal = load_config("portal")
    day = date.fromisoformat(argv[1]) if len(argv) > 1 else datetime.now(SEOUL).date()

    with connect() as conn, transaction(conn) as cur:
        report = gather(cur, day, swing, portal)

    body = render(report)
    print(body)

    try:
        if notifier is None:
            notifier = TelegramNotifier.from_env()
    except RuntimeError:
        logger.exception("알림 설정이 없어 보고를 보내지 못했습니다")
        return 1
    return 0 if notifier.send(level(report), "매매 요약", body) else 1


def _today(rows: list[Any], day: date) -> list[Any]:
    """그날(KST) 것만 남긴다. 조회 함수들이 건수로만 자르기 때문이다."""
    return [r for r in rows if r.created_at.astimezone(SEOUL).date() == day]


def _engine(state: ProcessState | None, stale_sec: int) -> str:
    if state is None:
        return "기록 없음"
    mark = "멈춤?" if state.age_seconds > stale_sec else state.status
    halt = (state.detail or {}).get("halt_entry")
    tail = ", 진입 차단됨" if halt else ""
    return f"{mark} (박동 {int(state.age_seconds)}초 전){tail}"


def _order(order: OrderView) -> str:
    """체결 수량을 함께 적는다. **부분체결과 완전체결이 상태로는 안 갈린다.**"""
    line = f"- {order.side} {order.name or order.stock_id} {order.quantity}주"
    line += f" {order.status} ({order.filled_qty}주 체결"
    if order.avg_fill_price:
        line += f" @{order.avg_fill_price:,.0f}"
    line += ")"
    return line + (f" {order.error_message}" if order.error_message else "")


def _position(position: PositionView) -> str:
    """마지막 종가는 **장중에는 전날 것**이다 (PositionView 설명)."""
    line = f"- {position.name or position.stock_id} {position.quantity}주"
    line += f" 평단 {position.avg_price:,.0f}"
    if position.last_close:
        rate = (position.last_close - position.avg_price) / position.avg_price * 100
        line += f" 종가 {position.last_close:,.0f} ({rate:+.1f}%)"
    return line


def _pnl(row: PnlRow) -> str:
    parts = []
    if row.total_asset is not None:
        parts.append(f"총자산 {row.total_asset:,.0f}")
    if row.unrealized_pnl is not None:
        parts.append(f"평가손익 {row.unrealized_pnl:+,.0f}")
    if row.trade_count is not None:
        parts.append(f"거래 {row.trade_count}건")
    return "- " + " · ".join(parts) if parts else "- 값 없음"


if __name__ == "__main__":
    sys.exit(main(sys.argv))
