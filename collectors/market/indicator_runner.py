# 지표 수집기들을 돌리는 실행기. 하나가 실패해도 나머지는 돈다

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from common.db.conn import connect, transaction
from common.db.events import log_event
from common.db.indicators import (
    recent_failures,
    recompute_change_rate,
    touch_source,
    upsert_indicator_values,
)
from common.notify.base import Notifier

from ..base import Collector

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunOutcome:
    name: str
    success: bool
    records: int
    error: str | None = None


def process_name(collector: Collector) -> str:
    """event_log 에 남길 이름. 소스와 클래스를 함께 적는다."""
    return f"{collector.source_kind}.{type(collector).__name__}"


def store(collector: Collector, records: list) -> None:
    """지표값을 넣고 변화율을 다시 계산한다."""
    if not records:
        return
    with connect() as conn, transaction(conn) as cur:
        upsert_indicator_values(cur, records)
        for code in {r.indicator_code for r in records}:
            recompute_change_rate(cur, code)
        touch_source(cur, collector.source_kind, collector.source_identifier)


def run(
    collectors: list[Collector],
    since: datetime,
    *,
    notifier: Notifier | None = None,
    failure_window_hours: int = 1,
    failure_threshold: int = 2,
) -> list[RunOutcome]:
    """수집기를 차례로 돌리고 결과를 돌려준다.

    실패는 `event_log` 에 남기고, 창 안에서 임계를 넘으면 알린다.
    수집기 실패 알림은 '1시간 내 반복 시' 다 (PROJECT.md 10장).
    한 번 실패로 울리면 일시 장애마다 알림이 온다.
    """
    outcomes = []

    for collector in collectors:
        name = process_name(collector)
        try:
            result = collector.collect(since)
            outcome = (
                RunOutcome(name, success=True, records=len(result.records))
                if result.success
                else RunOutcome(name, success=False, records=0, error=result.error)
            )
            if result.success:
                store(collector, result.records)
        except Exception as exc:
            logger.exception("%s 수집 실패", name)
            outcome = RunOutcome(name, success=False, records=0, error=repr(exc))

        outcomes.append(outcome)
        _record(outcome, notifier, failure_window_hours, failure_threshold)

    return outcomes


def _record(
    outcome: RunOutcome,
    notifier: Notifier | None,
    window_hours: int,
    threshold: int,
) -> None:
    with connect() as conn, transaction(conn) as cur:
        log_event(
            cur,
            outcome.name,
            "INFO" if outcome.success else "ERROR",
            "지표 수집",
            category="collect",
            detail={"records": outcome.records, "error": outcome.error},
        )
        if outcome.success:
            return
        failures = recent_failures(cur, outcome.name, window_hours)

    if failures < threshold or notifier is None:
        return
    notifier.send(
        "ERROR",
        "수집기 반복 실패",
        f"{outcome.name}\n{window_hours}시간 내 {failures}회\n{outcome.error}",
    )
