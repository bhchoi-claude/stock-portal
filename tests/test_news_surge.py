# 급등 집계. 기준선 계산과 첫 등장 처리를 확인한다

from datetime import date
from decimal import Decimal

from collectors.news.aggregate import days_to_rebuild, describe
from common.db.keywords import (
    Surge,
    aggregate_day,
    daily_ranked,
    refresh_surge,
    surging,
)

TODAY = date(2026, 8, 30)

# 정렬 테스트 전용. 실제 집계가 없는 날이라 우리 키워드만 줄에 선다.
# 실제 데이터가 있는 날을 쓰면 상위 10 이 진짜 급등어로 채워져 밀린다.
# 기준선도 이 날의 직전 이틀이어야 ma7 에 잡힌다
QUIET = date(1999, 1, 4)
QUIET_BEFORE = (date(1999, 1, 2), date(1999, 1, 3))


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

    # 직전 7일(08-23~08-29)에 1회 -> 하루 평균 1/7.
    # ma7 이 NUMERIC(10,2) 라 0.14 로 저장된다
    assert ma7 == Decimal("0.14")
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


def test_화면은_절대_빈도가_아니라_급등도로_줄_세운다(cur):
    """2026-09-01 까지 mention_count 순이었다.

    늘 많이 나오는 'AI'(39회, 2.97배)가 상위를 차지하고, 그날의 신호인
    '폴더블'(9회, 63배)이 아래에 묻혔다. `SCHEMA.md` 가 "절대 빈도가 아니라
    surge_ratio 가 신호다" 라고 못박고 있다.
    """
    common = _keyword(cur, "흔한말")
    rare = _keyword(cur, "드문말")
    for day in QUIET_BEFORE:
        _daily(cur, common, day, 40)
        _daily(cur, rare, day, 1)
    _daily(cur, common, QUIET, 39)  # 평소만큼
    _daily(cur, rare, QUIET, 9)  # 평소의 몇 배
    refresh_surge(cur, QUIET)

    terms = [row.term for row in daily_ranked(cur, QUIET, 10)]

    # 건수는 흔한말이 네 배 많지만 위에 오는 것은 드문말이다
    assert terms.index("드문말") < terms.index("흔한말")


def test_처음_나온_말은_건수를_배수_자리에_쓴다(cur):
    """`ma7` 이 0 이라 배수를 낼 수 없다. NULLS LAST 로 밀면 새 테마가 묻힌다.

    0회에서 10회가 된 것은 10배 급등과 크기가 비슷하다.
    """
    fresh = _keyword(cur, "오늘처음본말")
    quiet = _keyword(cur, "밋밋한말")
    for day in QUIET_BEFORE:
        _daily(cur, quiet, day, 10)
    _daily(cur, fresh, QUIET, 10)  # 배수 없음, 건수 10
    _daily(cur, quiet, QUIET, 12)  # 배수 약 1.2
    refresh_surge(cur, QUIET)

    terms = [row.term for row in daily_ranked(cur, QUIET, 10)]

    assert terms.index("오늘처음본말") < terms.index("밋밋한말")


def test_한_번_나온_신규는_위로_안_온다(cur):
    """건수를 배수 자리에 쓰므로 1회는 1배와 같은 취급이다."""
    once = _keyword(cur, "한번나온말")
    surging_term = _keyword(cur, "튀어오른말")
    for day in QUIET_BEFORE:
        _daily(cur, surging_term, day, 1)
    _daily(cur, once, QUIET, 1)
    _daily(cur, surging_term, QUIET, 8)
    refresh_surge(cur, QUIET)

    terms = [row.term for row in daily_ranked(cur, QUIET, 10)]

    assert terms.index("튀어오른말") < terms.index("한번나온말")


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
