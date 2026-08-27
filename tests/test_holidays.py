# 일봉에서 휴장일을 역산하는 규칙을 확인한다

from datetime import date

from collectors.market.holidays import missing_weekdays


def test_거래가_없던_평일이_휴장일이다():
    # 2026-08-17 은 월요일이고 광복절 대체공휴일이다
    traded = {date(2026, 8, 14), date(2026, 8, 18)}

    days = missing_weekdays(date(2026, 8, 14), date(2026, 8, 18), traded)

    assert days == [date(2026, 8, 17)]


def test_주말은_담지_않는다():
    # 8/15 토, 8/16 일. 거래가 없지만 휴장일 표에 넣지 않는다
    traded = {date(2026, 8, 14), date(2026, 8, 17), date(2026, 8, 18)}

    days = missing_weekdays(date(2026, 8, 14), date(2026, 8, 18), traded)

    assert days == []


def test_구간_경계를_포함한다():
    days = missing_weekdays(date(2026, 8, 17), date(2026, 8, 19), set())

    assert days == [date(2026, 8, 17), date(2026, 8, 18), date(2026, 8, 19)]


def test_전부_거래일이면_비어_있다():
    traded = {date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26)}

    assert missing_weekdays(date(2026, 8, 24), date(2026, 8, 26), traded) == []
