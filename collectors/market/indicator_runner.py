# 지표 수집기들을 돌리는 실행기. 하나가 실패해도 나머지는 돈다

from __future__ import annotations

import logging
import math
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
    """event_log 에 남길 이름. 수집기마다 달라야 한다.

    같은 클래스를 여러 지표에 쓰면 클래스명만으로는 구분이 안 되고,
    실패 집계가 섞여 한쪽 장애가 다른 쪽 알림을 당긴다.
    지표 코드가 있으면 그것을 쓴다.
    """
    label = getattr(collector, "indicator_code", None) or type(collector).__name__
    return f"{collector.source_kind}.{label}"


def failure_window_hours(interval_sec: int, threshold: int, minimum: int) -> int:
    """연속 `threshold` 회 실패를 담을 만큼의 관찰 창(시간).

    PROJECT.md 10장은 '1시간 내 반복 시' 라고 적었지만 그것은 자주 도는
    수집기를 전제한 표현이다. 하루 한 번 도는 수집기는 1시간 창 안에서
    최대 1회만 실패하므로 임계값 2 에 영원히 닿지 못한다.
    **완전히 죽은 소스가 영원히 조용해진다.**

    주기 x 임계값을 창으로 잡으면 '최근 N 번 연속 실패' 를 잡는다.
    """
    return max(minimum, math.ceil(interval_sec * threshold / 3600))


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
    min_window_hours: int = 1,
    failure_threshold: int = 2,
) -> list[RunOutcome]:
    """수집기를 차례로 돌리고 결과를 돌려준다.

    실패는 `event_log` 에 남기고, 창 안에서 임계를 넘으면 알린다.
    한 번 실패로 울리면 일시 장애마다 알림이 온다.
    창은 수집기 주기에 맞춰 잡는다 (`failure_window_hours` 참조).
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
        window = failure_window_hours(
            collector.interval_sec, failure_threshold, min_window_hours
        )
        _record(outcome, notifier, window, failure_threshold)

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
