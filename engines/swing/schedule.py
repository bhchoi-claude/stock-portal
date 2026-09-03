# 하루 시간표 판정. 상주 루프에서 시각 분기만 떼어낸 순수 함수다

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

# 시간 순서대로 둔다. 같은 틱에 둘 이상이 걸리면 이 순서로 실행된다
TASKS: tuple[str, ...] = ("submit", "cancel", "snapshot", "plan")


@dataclass(frozen=True)
class Timetable:
    """엔진이 하루에 하는 일과 그 시각(KST). `config/engine.yaml` 에서 온다."""

    submit: time  # 전날 계획을 장 시작 동시호가에 낸다
    cancel: time  # 미체결 잔량을 취소한다
    snapshot: time  # daily_pnl 을 찍는다
    plan: time  # scan·manage 로 다음 날 계획을 만든다

    def at(self, task: str) -> time:
        return getattr(self, task)


def due_tasks(
    now: datetime,
    table: Timetable,
    done: dict[str, date],
    retry_at: dict[str, datetime] | None = None,
    window_min: int = 30,
) -> list[str]:
    """지금 실행해야 할 일. **시각이 지났고 오늘 아직 안 한 것**이다.

    '정각에 깨운다' 가 아니라 '지났는데 안 했으면 한다' 이다. 틱이 10초라
    정각을 놓칠 일은 없지만, **늦게 시작하거나 재시작해도 그날 남은 일이
    실행된다.** 15:00 에 올라온 프로세스가 08:30 제출을 건너뛰고 15:30
    취소부터 하는 것이 옳다.

    `now` 는 **한국 시각**이어야 한다. 거래일이 시장 현지 기준이고
    시간표도 KST 로 적혀 있다 (CLAUDE.md 5).

    **창을 벗어난 일은 건너뛴다.** `done` 은 프로세스 메모리에만 있어
    재시작하면 비는데, 창이 없으면 21:03 에 올라온 엔진이 09:00 제출을
    그때 해버린다. 2026-09-03 첫 기동에서 실제로 그랬다 — 계획 두 건이
    장종료로 거부되며 소진됐다.

    창이 곧 위 문장의 구현이다. 15:00 에 올라오면 제출 창(09:00~09:30)이
    이미 지나 건너뛰고, 취소 창은 아직이라 15:10 에 한다.

    `retry_at` 에 있는 일은 그 시각 전까지 걸리지 않는다. 19:00 에 일봉이
    아직 안 쌓였을 때 20분 뒤 다시 보게 하는 자리다. 다시 볼 일을
    `done` 에 넣으면 그날이 끝나버려 쓸 수 없다.

    **다시 보기로 한 일에는 창을 적용하지 않는다.** 일봉을 두 시간까지
    기다리는데 30분 창으로 자르면 그 기다림이 무의미해진다.

    **토·일에는 아무것도 하지 않는다.** 공휴일은 달력으로 거르지 않는다 —
    `exchange_holiday` 는 일봉에서 역산해 채우므로 앞으로의 휴일을 모른다.
    휴일은 데이터로 막는다. 계획은 그것을 만든 일봉이 여전히 최신일 때만
    유효하고(`engine.plan_basis`), 일봉이 없는 날은 판단을 건너뛴다.
    """
    if now.weekday() >= 5:
        return []

    waits = retry_at or {}
    return [
        task for task in TASKS if _is_due(now, table, done, waits, task, window_min)
    ]


def _is_due(
    now: datetime,
    table: Timetable,
    done: dict[str, date],
    waits: dict[str, datetime],
    task: str,
    window_min: int,
) -> bool:
    if done.get(task) == now.date():
        return False

    if task in waits:
        # 다시 보기로 한 것이다. 창을 다시 재지 않는다
        return now >= waits[task]

    opens = datetime.combine(now.date(), table.at(task), tzinfo=now.tzinfo)
    return opens <= now < opens + timedelta(minutes=window_min)
