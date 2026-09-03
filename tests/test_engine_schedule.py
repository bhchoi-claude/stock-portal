# 시간표 판정. DB 없이 도는 순수 함수라 경계 조건을 전부 본다

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from engines.swing.schedule import TASKS, Timetable, due_tasks

SEOUL = ZoneInfo("Asia/Seoul")

# config/engine.yaml 의 실측 확정 시각과 같게 둔다
TABLE = Timetable(
    submit=time(9, 0),
    cancel=time(15, 10),
    snapshot=time(15, 40),
    plan=time(19, 0),
)

TUE = date(2026, 9, 1)
SAT = date(2026, 9, 5)
SUN = date(2026, 9, 6)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SEOUL)


# --- 창 안에서만 걸린다 -------------------------------------------------------


def test_시각_전에는_안_걸린다():
    assert due_tasks(at(TUE, 8, 59), TABLE, {}) == []


def test_정각에_걸린다():
    assert due_tasks(at(TUE, 9, 0), TABLE, {}) == ["submit"]


def test_창_안이면_걸린다():
    assert due_tasks(at(TUE, 9, 29), TABLE, {}) == ["submit"]


def test_창을_벗어나면_건너뛴다():
    """**2026-09-03 첫 기동에서 여기가 없어 사고가 났다.**

    21:03 에 재시작하니 `done` 이 비어 09:00 제출을 그때 해버렸다. 계획
    두 건이 장종료로 거부되며 소진됐다.

    docstring 은 처음부터 "15:00 에 올라온 프로세스가 08:30 제출을
    건너뛰고" 라고 적혀 있었는데 코드가 그러지 않았다.
    """
    assert due_tasks(at(TUE, 9, 30), TABLE, {}) == []
    assert due_tasks(at(TUE, 21, 3), TABLE, {}) == []


def test_늦게_올라와도_남은_일은_한다():
    """15:00 에 올라오면 제출은 지났고 취소는 아직이다."""
    assert due_tasks(at(TUE, 15, 15), TABLE, {}) == ["cancel"]
    assert due_tasks(at(TUE, 15, 45), TABLE, {}) == ["snapshot"]
    assert due_tasks(at(TUE, 19, 10), TABLE, {}) == ["plan"]


# --- 하루에 한 번 -------------------------------------------------------------


def test_한_일은_다시_안_걸린다():
    assert due_tasks(at(TUE, 9, 10), TABLE, {"submit": TUE}) == []


def test_어제_한_것은_오늘_다시_걸린다():
    assert due_tasks(
        at(TUE, 9, 10), TABLE, dict.fromkeys(TASKS, date(2026, 8, 31))
    ) == ["submit"]


def test_주말에는_아무것도_안_한다():
    assert due_tasks(at(SAT, 9, 10), TABLE, {}) == []
    assert due_tasks(at(SUN, 19, 10), TABLE, {}) == []


# --- 다시 보기는 창을 무시한다 -------------------------------------------------


def test_retry_at_이_지나기_전에는_안_걸린다():
    """19:00 에 일봉이 없어 20분 뒤로 미룬 상태다."""
    retry = {"plan": at(TUE, 19, 20)}

    assert due_tasks(at(TUE, 19, 5), TABLE, {}, retry) == []
    assert due_tasks(at(TUE, 19, 20), TABLE, {}, retry) == ["plan"]


def test_다시_보기로_한_일에는_창을_안_잰다():
    """일봉을 두 시간까지 기다리는데 30분 창으로 자르면 기다림이 무의미해진다."""
    retry = {"plan": at(TUE, 20, 40)}

    assert due_tasks(at(TUE, 20, 40), TABLE, {}, retry) == ["plan"]


def test_창_길이를_바꿀_수_있다():
    assert due_tasks(at(TUE, 9, 45), TABLE, {}) == []
    assert due_tasks(at(TUE, 9, 45), TABLE, {}, window_min=60) == ["submit"]


def test_같은_틱에_둘이_걸리면_시간_순서다():
    """창이 겹치는 시간표에서만 일어난다. 순서가 곧 실행 순서다."""
    tight = Timetable(
        submit=time(9, 0), cancel=time(9, 5), snapshot=time(9, 10), plan=time(9, 15)
    )

    assert due_tasks(at(TUE, 9, 20), tight, {}) == list(TASKS)


def test_시각표_상수가_시간_순이다():
    """`TASKS` 순서대로 실행된다. 설정 시각과 어긋나면 안 된다."""
    times = [TABLE.at(task) for task in TASKS]
    assert times == sorted(times)


def test_창의_끝은_열려_있다():
    """정각 + 창 길이는 이미 지난 것으로 본다."""
    opens = at(TUE, 9, 0)
    assert due_tasks(opens + timedelta(minutes=29, seconds=59), TABLE, {}) == ["submit"]
    assert due_tasks(opens + timedelta(minutes=30), TABLE, {}) == []
