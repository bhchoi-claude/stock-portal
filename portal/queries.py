# 화면과 API 가 함께 쓰는 조회 함수. DB 값을 JSON 으로 낼 수 있는 형태로 바꾼다

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from common.config import load_config
from common.db.backtest import RunRow, recent_runs
from common.db.commands import CommandView, recent_commands
from common.db.conn import connect, transaction
from common.db.disclosures import Disclosure, recent_disclosures
from common.db.events import EventRow, recent_events
from common.db.filters import FilterRow, list_filters
from common.db.heartbeat import ProcessState, list_heartbeats
from common.db.indicators import IndicatorSnapshot, indicator_snapshot
from common.db.keywords import DailyKeyword, daily_ranked, merge_keywords
from common.db.messages import MessageView, collection_status, messages_for_keyword
from common.db.orders import OrderView, recent_orders
from common.db.pnl import PnlRow, recent_pnl
from common.db.positions import PositionView, position_views
from common.db.regime import RegimeRow, current_regime, regime_history
from common.db.signals import SignalView, open_signals

# 거래일은 시장 현지 기준이다. 서버 시계가 어디에 있든 KST 로 센다
SEOUL = ZoneInfo("Asia/Seoul")


def dashboard() -> dict[str, Any]:
    """대시보드 한 번 호출로 필요한 것 전부 (INTERFACES.md 10장).

    매매 항목은 비어 있다. 엔진이 Phase 8 에 붙는다.
    """
    with read_cursor() as cur:
        return {
            "regime": _regime(current_regime(cur)),
            "indicators": [_indicator(i) for i in indicator_snapshot(cur)],
            "processes": _processes(cur),
            "trading": {"accounts": [], "positions": [], "executions": []},
        }


def regime_now() -> dict[str, Any] | None:
    with read_cursor() as cur:
        return _regime(current_regime(cur))


def regime_range(start: date | None, end: date | None) -> dict[str, Any]:
    """구간 판정 이력. 구간을 주지 않으면 설정의 기본 일수만큼 거슬러 본다."""
    params = load_config("portal")
    end = end or datetime.now(SEOUL).date()
    start = start or end - timedelta(days=params["regime_history_days"])
    with read_cursor() as cur:
        rows = regime_history(cur, start, end)
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "rows": [_regime(r) for r in rows],
    }


def keywords_surge(day: date | None) -> dict[str, Any]:
    """그날 키워드 순위. 급등 판정은 알림과 같은 임계값을 쓴다."""
    rules = load_config("collect")["news"]["surge"]
    params = load_config("portal")
    day = day or datetime.now(SEOUL).date()
    with read_cursor() as cur:
        rows = daily_ranked(cur, day, params["keywords_limit"], rules["min_count"])
    return {
        "date": day.isoformat(),
        "rows": [_keyword(row, rules) for row in rows],
    }


def messages(term: str, since: date | None, limit: int | None) -> dict[str, Any]:
    """키워드가 나온 원문. 화면에서 근거를 확인하는 자리다."""
    params = load_config("portal")
    since = since or datetime.now(SEOUL).date() - timedelta(
        days=params["messages_days"]
    )
    capped = min(limit or params["messages_limit"], params["messages_limit"])
    start = datetime.combine(since, time.min, tzinfo=SEOUL)
    with read_cursor() as cur:
        rows = messages_for_keyword(cur, term, start, capped)
    return {
        "keyword": term,
        "from": since.isoformat(),
        "rows": [_message(row) for row in rows],
    }


def disclosures(limit: int | None) -> list[dict[str, Any]]:
    """최근 전자공시. 유형 코드가 이미 분류라 LLM 을 쓰지 않는다."""
    params = load_config("portal")
    capped = min(limit or params["disclosures_limit"], params["disclosures_limit"])
    with read_cursor() as cur:
        return [_disclosure(row) for row in recent_disclosures(cur, capped)]


def channels() -> list[dict[str, Any]]:
    with read_cursor() as cur:
        return [
            {"name": name, "messages": count, "last_published_at": _ts(last)}
            for name, count, last in collection_status(cur)
        ]


def merge(into: int, from_ids: list[int]) -> int:
    """동의어 병합. 화면에서 하는 유일한 쓰기 동작이다 (INTERFACES.md 10장)."""
    with read_cursor() as cur:
        return merge_keywords(cur, into, from_ids)


def indicators() -> list[dict[str, Any]]:
    with read_cursor() as cur:
        return [_indicator(i) for i in indicator_snapshot(cur)]


def processes() -> list[dict[str, Any]]:
    with read_cursor() as cur:
        return _processes(cur)


def events(levels: Sequence[str] | None, limit: int | None) -> list[dict[str, Any]]:
    """최근 이벤트. limit 은 설정의 상한을 넘지 못한다."""
    params = load_config("portal")
    capped = min(limit or params["events_limit_default"], params["events_limit_max"])
    with read_cursor() as cur:
        return [_event(e) for e in recent_events(cur, levels=levels, limit=capped)]


def backtest_runs(limit: int | None) -> list[dict[str, Any]]:
    """백테스트 실행 목록. 운영·로그 탭에서 지표를 나란히 본다.

    실행은 CLI 로만 한다. **화면에서 백테스트를 돌리는 버튼은 만들지 않는다**
    (PROJECT.md 11장, 포털은 조회 전용이다).
    """
    params = load_config("portal")
    capped = min(limit or params["backtest_runs_limit"], params["backtest_runs_max"])
    with read_cursor() as cur:
        return [_run(r) for r in recent_runs(cur, capped)]


@contextmanager
def read_cursor() -> Iterator[psycopg.Cursor]:
    """요청 하나에 커넥션 하나. DB 가 같은 서버라 풀을 두지 않는다.

    조회 전용이지만 transaction() 으로 연다. 커서를 직접 만들어 쓰지 않는 것이
    이 프로젝트의 규약이다 (common/db/conn.py).
    """
    with connect() as conn, transaction(conn) as cur:
        yield cur


def _processes(cur: psycopg.Cursor) -> list[dict[str, Any]]:
    """설정에 적은 프로세스를 먼저, 설정에 없이 신호만 있는 것을 뒤에 붙인다."""
    params = load_config("portal")
    states = {s.process_name: s for s in list_heartbeats(cur)}

    rows = []
    for entry in params["processes"]:
        state = states.pop(entry["name"], None)
        rows.append(
            _process(entry["name"], entry["label"], state, entry["stale_after_minutes"])
        )

    # 설정에 없는 프로세스도 숨기지 않는다. 엔진이 붙는 날 바로 보인다
    for name, state in states.items():
        rows.append(_process(name, name, state, params["default_stale_after_minutes"]))
    return rows


def _process(
    name: str, label: str, state: ProcessState | None, stale_after_minutes: int
) -> dict[str, Any]:
    """화면이 쓰는 상태를 하나 더 만든다.

    heartbeat 의 status 는 프로세스가 스스로 적은 것이고, state 는
    '지금 이것을 어떻게 봐야 하는가' 다. 신호가 끊긴 running 은 죽은 것이다.
    """
    if state is None:
        derived = "unknown"
    elif state.status == "error":
        derived = "error"
    elif state.age_seconds > stale_after_minutes * 60:
        derived = "stale"
    else:
        derived = "ok"

    return {
        "name": name,
        "label": label,
        "state": derived,
        "status": state.status if state else None,
        "last_beat_at": _ts(state.last_beat_at) if state else None,
        "started_at": _ts(state.started_at) if state else None,
        "age_seconds": round(state.age_seconds) if state else None,
        "stale_after_minutes": stale_after_minutes,
        "detail": state.detail if state else None,
    }


def _keyword(row: DailyKeyword, rules: dict[str, Any]) -> dict[str, Any]:
    """급등 여부를 함께 낸다. 화면이 임계값을 따로 알 필요가 없다."""
    is_new = row.ma7 is not None and row.ma7 == 0
    surging = bool(
        row.surge_ratio is not None
        and row.ma7 is not None
        and row.ma7 >= Decimal(str(rules["min_baseline"]))
        and row.surge_ratio >= Decimal(str(rules["min_ratio"]))
    ) or bool(is_new and row.mention_count >= rules["new_min_count"])

    return {
        "keyword_id": row.keyword_id,
        "term": row.term,
        "mention_count": row.mention_count,
        "weighted_count": _num(row.weighted_count),
        "ma7": _num(row.ma7),
        "surge_ratio": _num(row.surge_ratio),
        "is_new": is_new,
        "is_surging": surging,
        "is_confirmed": row.is_confirmed,
    }


def _disclosure(row: Disclosure) -> dict[str, Any]:
    return {
        "rcept_no": row.rcept_no,
        "stock_id": row.stock_id,
        "corp_name": row.corp_name,
        "report_name": row.report_name,
        "disclosure_type": row.disclosure_type,
        "submitted_at": _ts(row.submitted_at),
        "url": row.url,
    }


def _message(row: MessageView) -> dict[str, Any]:
    return {
        "message_id": row.message_id,
        "source": row.source_name,
        "content": row.content,
        "published_at": _ts(row.published_at),
    }


def _regime(row: RegimeRow | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "trade_date": _day(row.trade_date),
        "regime": row.regime,
        "score": _num(row.score),
        "layer_scores": row.layer_scores,
        "indicators": row.indicators,
        "rule_version": row.rule_version,
        "is_override": row.is_override,
        "override_reason": row.override_reason,
        "kospi_return": _num(row.kospi_return),
        "kosdaq_return": _num(row.kosdaq_return),
    }


def _indicator(row: IndicatorSnapshot) -> dict[str, Any]:
    return {
        "indicator_code": row.indicator_code,
        "name": row.name,
        "layer": row.layer,
        "frequency": row.frequency,
        "unit": row.unit,
        "use_in_regime": row.use_in_regime,
        "period_date": _day(row.period_date),
        "value": _num(row.value),
        "change_rate": _num(row.change_rate),
    }


def _event(row: EventRow) -> dict[str, Any]:
    return {
        "event_id": row.event_id,
        "process_name": row.process_name,
        "level": row.level,
        "category": row.category,
        "message": row.message,
        "detail": row.detail,
        "created_at": _ts(row.created_at),
    }


def _run(row: RunRow) -> dict[str, Any]:
    """`note` 를 반드시 함께 낸다. 생존편향 경고가 거기에 있다.

    지표만 떼어 보내면 화면이 경고 없이 숫자만 띄우게 된다 (2026-08-30 승인).
    """
    return {
        "run_id": row.run_id,
        "strategy": row.strategy,
        "from_date": _day(row.from_date),
        "to_date": _day(row.to_date),
        "initial_capital": _num(row.initial_capital),
        "final_capital": _num(row.final_capital),
        "total_return": _num(row.total_return),
        "mdd": _num(row.mdd),
        "win_rate": _num(row.win_rate),
        "trade_count": row.trade_count,
        "sharpe": _num(row.sharpe),
        "fee_rate": _num(row.fee_rate),
        "slippage_rate": _num(row.slippage_rate),
        "note": row.note,
        "created_at": _ts(row.created_at),
    }


def _num(value: Decimal | None) -> str | None:
    """Decimal 을 문자열로 낸다. float 로 바꾸면 값이 미세하게 달라진다."""
    return None if value is None else str(value)


def _day(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _ts(value: datetime | None) -> str | None:
    """UTC 로 저장된 시각을 그대로 낸다. 화면에서 현지 시각으로 바꾼다."""
    return None if value is None else value.isoformat()


def trading() -> dict[str, Any]:
    """자동매매 탭 한 번 호출로 필요한 것 전부.

    **브로커를 부르지 않는다.** 엔진이 DB 에 남긴 것만 본다 — 포털과 엔진의
    통신은 DB 로만 한다 (CLAUDE.md 8). 그래서 잔고는 마지막 `daily_pnl`
    스냅샷(15:40)이고 장중에는 그 시점 값이다.
    """
    engine = load_config("engine")["swing"]
    limits = load_config("portal")["trading"]
    account_id = engine["account_id"]
    process_name = engine["process_name"]
    today = datetime.now(SEOUL).date()

    with read_cursor() as cur:
        states = {s.process_name: s for s in list_heartbeats(cur)}
        pnl = recent_pnl(cur, account_id, limits["pnl_days"])
        return {
            "account_id": account_id,
            "process_name": process_name,
            "engine": _engine_state(states.get(process_name)),
            "pnl": [_pnl(row) for row in pnl],
            "positions": [_position(p) for p in position_views(cur, account_id)],
            "signals": [
                _signal(s)
                for s in open_signals(cur, engine["strategy"], limits["signals_limit"])
            ],
            "orders": [
                _order(o)
                for o in recent_orders(cur, account_id, limits["orders_limit"])
            ],
            "commands": [
                _command(c)
                for c in recent_commands(cur, process_name, limits["commands_limit"])
            ],
            "filters": [_filter(f, today) for f in list_filters(cur)],
        }


def _engine_state(state: ProcessState | None) -> dict[str, Any]:
    """엔진 상태와 진입차단 여부.

    `halt_entry` 는 heartbeat 의 `detail` 에 들어 있다. 별도 테이블을 두지
    않은 것은 이 값이 프로세스의 상태이지 영속 설정이 아니기 때문이다 —
    엔진이 재시작하면 대조를 다시 하고 새로 판단한다.
    """
    if state is None:
        return {"status": None, "halt_entry": None, "last_beat_at": None}
    detail = state.detail or {}
    return {
        "status": state.status,
        "halt_entry": detail.get("halt_entry"),
        "last_beat_at": _ts(state.last_beat_at),
        "started_at": _ts(state.started_at),
        "age_seconds": round(state.age_seconds),
    }


def _pnl(row: PnlRow) -> dict[str, Any]:
    return {
        "trade_date": _day(row.trade_date),
        "deposit": _num(row.deposit),
        "eval_amount": _num(row.eval_amount),
        "total_asset": _num(row.total_asset),
        "realized_pnl": _num(row.realized_pnl),
        "unrealized_pnl": _num(row.unrealized_pnl),
        "trade_count": row.trade_count,
    }


def _position(row: PositionView) -> dict[str, Any]:
    """평가금액과 손익은 여기서 낸다. 마지막 종가 기준이라 장중에는 전날 값이다."""
    value = row.last_close * row.quantity if row.last_close is not None else None
    cost = row.avg_price * row.quantity
    return {
        "stock_id": row.stock_id,
        "name": row.name,
        "quantity": row.quantity,
        "avg_price": _num(row.avg_price),
        "last_close": _num(row.last_close),
        "cost": _num(cost),
        "value": _num(value),
        "pnl": _num(value - cost) if value is not None else None,
        # 0 으로 나누지 않는다. 평단가가 0 인 포지션은 있을 수 없지만 방어한다
        "pnl_rate": _num((value - cost) / cost) if value is not None and cost else None,
        "opened_at": _ts(row.opened_at),
        "synced_at": _ts(row.synced_at),
    }


def _signal(row: SignalView) -> dict[str, Any]:
    return {
        "signal_id": row.signal_id,
        "stock_id": row.stock_id,
        "name": row.name,
        "side": row.side,
        "strength": _num(row.strength),
        "payload": row.payload,
        "regime_at": row.regime_at,
        "created_at": _ts(row.created_at),
    }


def _order(row: OrderView) -> dict[str, Any]:
    return {
        "order_id": row.order_id,
        "stock_id": row.stock_id,
        "name": row.name,
        "side": row.side,
        "order_type": row.order_type,
        "quantity": row.quantity,
        "price": _num(row.price),
        "status": row.status,
        "filled_qty": row.filled_qty,
        "avg_fill_price": _num(row.avg_fill_price),
        "error_message": row.error_message,
        "is_manual": row.is_manual,
        "created_at": _ts(row.created_at),
        "updated_at": _ts(row.updated_at),
    }


def _command(row: CommandView) -> dict[str, Any]:
    return {
        "command_id": row.command_id,
        "action": row.action,
        "params": row.params,
        "status": row.status,
        "issued_by": row.issued_by,
        "result": row.result,
        "created_at": _ts(row.created_at),
        "completed_at": _ts(row.completed_at),
    }


def _filter(row: FilterRow, today: date) -> dict[str, Any]:
    return {
        "filter_id": row.filter_id,
        "stock_id": row.stock_id,
        "name": row.name,
        "strategy": row.strategy,
        "filter_type": row.filter_type,
        "reason": row.reason,
        "until_date": _day(row.until_date),
        # 만료된 것도 목록에 남긴다. 왜 막았는지가 기록이다
        "expired": row.until_date is not None and row.until_date < today,
        "created_at": _ts(row.created_at),
    }
