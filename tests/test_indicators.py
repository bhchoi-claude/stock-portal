# 지수에서 지표를 만드는 계산을 확인한다

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from collectors.market.indicators import (
    KospiMaGapCollector,
    VkospiCollector,
    daily_returns,
    ma_gap_records,
)
from common.broker.kiwoom import INDEX_SCALE, strip_sign
from common.types import IndexClose

START = date(2026, 1, 1)


def closes(values, code="001"):
    return [
        IndexClose(code, START + timedelta(days=i), Decimal(str(v)))
        for i, v in enumerate(values)
    ]


class FakeBroker:
    def __init__(self, bars):
        self._bars = bars

    def get_index_closes(self, index_code, end):
        return self._bars


def test_지수는_백배로_와서_나눠야_한다():
    # ka20006 이 678888 을 줄 때 ka20003 은 같은 값을 6788.88 로 준다.
    # 나누지 않으면 지수가 100 배로 들어간다
    assert strip_sign("678888") / INDEX_SCALE == Decimal("6788.88")
    assert strip_sign("5008") / INDEX_SCALE == Decimal("50.08")


def test_이격도는_이동평균_대비_퍼센트다():
    # 3일 이평이 10 이고 종가가 11 이면 이격도는 10%
    bars = closes([9, 10, 11])

    records = ma_gap_records(bars, window=3, since=START)

    assert len(records) == 1
    assert records[0].indicator_code == "KOSPI_MA200_GAP"
    assert records[0].period_date == date(2026, 1, 3)
    assert records[0].value == Decimal(10)


def test_이평_구간이_모자란_앞부분은_값이_없다():
    # 0 으로 채우면 초기 구간이 '이격도 0' 으로 보인다
    bars = closes([9, 10, 11, 12])

    records = ma_gap_records(bars, window=3, since=START)

    assert [r.period_date for r in records] == [date(2026, 1, 3), date(2026, 1, 4)]


def test_since_이전은_만들지_않는다():
    bars = closes([9, 10, 11, 12])

    records = ma_gap_records(bars, window=3, since=date(2026, 1, 4))

    assert [r.period_date for r in records] == [date(2026, 1, 4)]


def test_이동평균은_구간의_마지막_window_개를_쓴다():
    # 마지막 3개(10, 11, 12)의 평균은 11, 종가 12 -> 약 9.09%
    bars = closes([1, 10, 11, 12])

    records = ma_gap_records(bars, window=3, since=START)

    assert records[-1].value == (Decimal(12) - Decimal(11)) / Decimal(11) * 100


def test_변동성지수는_그대로_지표가_된다():
    bars = closes([50.08, 53.01], code="603")

    result = VkospiCollector(FakeBroker(bars), "603", date(2026, 1, 2)).collect(
        datetime(2026, 1, 1, tzinfo=UTC)
    )

    assert result.success is True
    assert [r.indicator_code for r in result.records] == ["VKOSPI", "VKOSPI"]
    assert result.records[0].value == Decimal("50.08")


def test_이평_구간보다_데이터가_적으면_실패로_돌려준다():
    # 예외를 던지면 다른 수집기까지 멈출 수 있다. 실패로 알린다
    result = KospiMaGapCollector(
        FakeBroker(closes([1, 2, 3])), "001", date(2026, 1, 3), window=200
    ).collect(datetime(2026, 1, 1, tzinfo=UTC))

    assert result.success is False
    assert "200" in result.error
    assert result.records == []


def test_등락률은_전일_대비_퍼센트다():
    bars = closes([100, 110, 99])

    returns = daily_returns(bars)

    assert returns[date(2026, 1, 2)] == Decimal(10)
    assert returns[date(2026, 1, 3)] == Decimal(-10)


def test_첫날은_등락률이_없다():
    bars = closes([100, 110])

    assert date(2026, 1, 1) not in daily_returns(bars)


def test_종가가_0_이면_등락률을_만들지_않는다():
    bars = closes([0, 110])

    assert daily_returns(bars) == {}
