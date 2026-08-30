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
from common.db.conn import connect, transaction
from common.db.disclosures import Disclosure, recent_disclosures
from common.db.events import EventRow, recent_events
from common.db.heartbeat import ProcessState, list_heartbeats
from common.db.indicators import IndicatorSnapshot, indicator_snapshot
from common.db.keywords import DailyKeyword, daily_ranked, merge_keywords
from common.db.messages import MessageView, collection_status, messages_for_keyword
from common.db.regime import RegimeRow, current_regime, regime_history

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
        rows = daily_ranked(cur, day, params["keywords_limit"])
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


def _num(value: Decimal | None) -> str | None:
    """Decimal 을 문자열로 낸다. float 로 바꾸면 값이 미세하게 달라진다."""
    return None if value is None else str(value)


def _day(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _ts(value: datetime | None) -> str | None:
    """UTC 로 저장된 시각을 그대로 낸다. 화면에서 현지 시각으로 바꾼다."""
    return None if value is None else value.isoformat()
