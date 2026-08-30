# 급등 집계. 기준선 계산과 첫 등장 처리를 확인한다

from datetime import date
from decimal import Decimal

import pytest

from collectors.news.aggregate import days_to_rebuild, describe
from common.db.keywords import Surge, aggregate_day, refresh_surge, surging

TODAY = date(2026, 8, 30)


def test_days_default_span():
    assert days_to_rebuild([], TODAY, 3) == [
        date(2026, 8, 28),
        date(2026, 8, 29),
        TODAY,
    ]


def test_days_from_given_start():
    """인자로 시작일을 주면 그날부터 오늘까지 전부 다시 집계한다."""
    days = days_to_rebuild(["x", "2026-08-27"], TODAY, 3)
    assert days[0] == date(2026, 8, 27)
    assert days[-1] == TODAY
    assert len(days) == 4


def test_describe_separates_new_from_surge():
    surge = Surge(1, "유리기판", 30, Decimal("4.3"), Decimal("7.0"), False)
    assert describe(surge) == "유리기판 30회 (평소 4.3회, 7.0배)"

    new = Surge(2, "HBM", 12, Decimal(0), None, True)
    assert describe(new) == "HBM 12회 (처음 등장)"


def test_missing_days_count_as_zero(cur):
    """언급이 없던 날도 0회로 센다.

    행이 있는 날만 평균 내면 어쩌다 한 번 나오는 표현의 기준선이 1.0 이 되어
    급등이 묻힌다.
    """
    keyword_id = _keyword(cur, "유리기판")
    # 8일 전에 한 번, 그리고 오늘 7번. 사이는 비어 있다
    _daily(cur, keyword_id, date(2026, 8, 23), 1)
    _daily(cur, keyword_id, TODAY, 7)

    refresh_surge(cur, TODAY)
    ma7, ratio = _row(cur, keyword_id, TODAY)

    # 직전 7일(08-23~08-29)에 1회 -> 하루 평균 1/7
    assert ma7 == pytest.approx(Decimal(1) / 7, rel=Decimal("0.01"))
    assert ratio > 40


def test_today_is_excluded_from_baseline(cur):
    """당일을 넣으면 오늘의 급등이 제 기준선을 스스로 끌어올린다."""
    keyword_id = _keyword(cur, "테스트키워드")
    _daily(cur, keyword_id, TODAY, 70)

    refresh_surge(cur, TODAY)
    ma7, ratio = _row(cur, keyword_id, TODAY)

    assert ma7 == 0
    assert ratio is None


def test_first_appearance_is_caught_by_count(cur):
    """기준선이 0 이면 비율을 낼 수 없다. 버리면 가장 강한 신호를 버린다."""
    keyword_id = _keyword(cur, "처음보는말")
    _daily(cur, keyword_id, TODAY, 9)
    refresh_surge(cur, TODAY)

    found = surging(
        cur,
        TODAY,
        min_ratio=Decimal(3),
        min_baseline=Decimal(1),
        new_min_count=5,
    )
    assert any(s.term == "처음보는말" and s.is_new for s in found)


def test_quiet_keyword_is_not_a_surge(cur):
    """하루 0.2회가 1회가 된 것을 5배 급등이라 부를 수 없다."""
    keyword_id = _keyword(cur, "조용한말")
    _daily(cur, keyword_id, date(2026, 8, 27), 1)
    _daily(cur, keyword_id, TODAY, 1)
    refresh_surge(cur, TODAY)

    found = surging(
        cur,
        TODAY,
        min_ratio=Decimal(3),
        min_baseline=Decimal(1),
        new_min_count=5,
    )
    assert all(s.term != "조용한말" for s in found)


def test_aggregate_day_counts_nothing_without_mentions(cur):
    assert aggregate_day(cur, date(2020, 1, 1)) == 0


def _keyword(cur, term: str) -> int:
    cur.execute("INSERT INTO keyword (term) VALUES (%s) RETURNING keyword_id", (term,))
    return cur.fetchone()[0]


def _daily(cur, keyword_id: int, day: date, count: int) -> None:
    cur.execute(
        "INSERT INTO keyword_daily (keyword_id, trade_date, mention_count)"
        " VALUES (%s, %s, %s)",
        (keyword_id, day, count),
    )


def _row(cur, keyword_id: int, day: date):
    cur.execute(
        "SELECT ma7, surge_ratio FROM keyword_daily"
        " WHERE keyword_id = %s AND trade_date = %s",
        (keyword_id, day),
    )
    return cur.fetchone()
