# 시간표 판정 테스트. DB 없이 도는 순수 함수라 경계 조건을 전부 본다

from datetime import date, datetime, time

from engines.swing.schedule import TASKS, Timetable, due_tasks

TABLE = Timetable(
    submit=time(8, 30),
    cancel=time(15, 30),
    snapshot=time(15, 40),
    plan=time(19, 0),
)

# 2026-09-01 은 화요일이다
TUE = date(2026, 9, 1)
SAT = date(2026, 9, 5)
SUN = date(2026, 9, 6)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute))


def test_시각_전에는_아무것도_안_한다():
    assert due_tasks(at(TUE, 8, 0), TABLE, {}) == []


def test_정각에_걸린다():
    assert due_tasks(at(TUE, 8, 30), TABLE, {}) == ["submit"]


def test_늦게_시작해도_그날_남은_일이_전부_걸린다():
    """15:00 에 올라온 프로세스가 08:30 제출을 건너뛰면 안 된다."""
    assert due_tasks(at(TUE, 19, 30), TABLE, {}) == list(TASKS)


def test_한_일은_다시_안_걸린다():
    done = {"submit": TUE, "cancel": TUE}
    assert due_tasks(at(TUE, 19, 30), TABLE, done) == ["snapshot", "plan"]


def test_어제_한_것은_오늘_다시_걸린다():
    done = dict.fromkeys(TASKS, date(2026, 8, 31))
    assert due_tasks(at(TUE, 19, 30), TABLE, done) == list(TASKS)


def test_같은_틱에_둘이_걸리면_시간_순서다():
    assert due_tasks(at(TUE, 15, 45), TABLE, {}) == ["submit", "cancel", "snapshot"]


def test_주말에는_아무것도_안_한다():
    assert due_tasks(at(SAT, 19, 30), TABLE, {}) == []
    assert due_tasks(at(SUN, 19, 30), TABLE, {}) == []


def test_retry_at_이_지나기_전에는_안_걸린다():
    """19:00 에 일봉이 없어 20분 뒤로 미룬 상태다."""
    retry = {"plan": at(TUE, 19, 20)}
    assert due_tasks(at(TUE, 19, 5), TABLE, {}, retry) == [
        "submit",
        "cancel",
        "snapshot",
    ]
    assert "plan" in due_tasks(at(TUE, 19, 20), TABLE, {}, retry)


def test_retry_at_은_다른_일을_막지_않는다():
    retry = {"plan": at(TUE, 23, 0)}
    assert due_tasks(at(TUE, 16, 0), TABLE, {}, retry) == [
        "submit",
        "cancel",
        "snapshot",
    ]
