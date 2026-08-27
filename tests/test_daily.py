# 일 1회 배치가 휴장일·미공개·실패를 구분하는지 확인한다

from datetime import date

import pytest

from collectors.market import daily


@pytest.fixture
def no_data(monkeypatch):
    """시세 API 가 빈 응답을 주도록 만든다."""
    monkeypatch.setattr(daily, "fetch", lambda path, api_id, bas_dd: [])


def test_시세가_없으면_받을_것이_없는_날이다(no_data):
    assert daily.is_holiday("20260815") is True


def test_시세가_있으면_거래일이다(monkeypatch):
    monkeypatch.setattr(daily, "fetch", lambda path, api_id, bas_dd: [{"ISU_CD": "1"}])

    assert daily.is_holiday("20260826") is False


def test_휴장일_판정에_시세_api_를_쓴다(monkeypatch):
    # 종목기본정보는 휴장일에도 응답하므로 판정에 쓸 수 없다
    seen = {}
    monkeypatch.setattr(
        daily, "fetch", lambda path, api_id, bas_dd: seen.update(api_id=api_id) or []
    )

    daily.is_holiday("20260815")

    assert seen["api_id"] == "stk_bydd_trd"


def test_받을_것이_없으면_마스터를_건드리지_않는다(no_data, monkeypatch):
    # 종목기본정보는 휴장일에도 응답한다. 돌리면 휴장일 스냅샷이 stock 에 들어간다
    called = []
    monkeypatch.setattr(
        daily.stock_status, "main", lambda argv: called.append("status") or 0
    )

    assert daily.load_day(date(2026, 8, 15)) == "nodata"
    assert called == []


def test_상태_갱신을_일봉보다_먼저_돌린다(monkeypatch):
    order = []
    monkeypatch.setattr(daily, "fetch", lambda path, api_id, bas_dd: [{"ISU_CD": "1"}])
    monkeypatch.setattr(
        daily.stock_status, "main", lambda argv: order.append("status") or 0
    )
    monkeypatch.setattr(
        daily.price_daily, "main", lambda argv: order.append("price") or 0
    )

    assert daily.load_day(date(2026, 8, 26)) == "loaded"
    assert order == ["status", "price"]


def test_일봉이_실패하면_failed(monkeypatch):
    monkeypatch.setattr(daily, "fetch", lambda path, api_id, bas_dd: [{"ISU_CD": "1"}])
    monkeypatch.setattr(daily.stock_status, "main", lambda argv: 0)
    monkeypatch.setattr(daily.price_daily, "main", lambda argv: 1)

    assert daily.load_day(date(2026, 8, 26)) == "failed"


def test_예외도_실패로_잡는다(monkeypatch):
    monkeypatch.setattr(daily, "fetch", lambda path, api_id, bas_dd: [{"ISU_CD": "1"}])

    def boom(argv):
        raise RuntimeError("네트워크")

    monkeypatch.setattr(daily.stock_status, "main", boom)

    assert daily.load_day(date(2026, 8, 26)) == "failed"


def test_가진_데이터보다_앞의_미적재일은_휴장일이다():
    # 8/17 이 비었지만 8/18 이 들어왔으므로 8/17 은 데이터가 없는 날이다
    pending = [date(2026, 8, 17), date(2026, 8, 27), date(2026, 8, 28)]

    stale = daily.stale_days(pending, date(2026, 8, 26))

    assert stale == [date(2026, 8, 27), date(2026, 8, 28)]


def test_데이터가_하나도_없으면_전부_지연이다():
    pending = [date(2026, 8, 17), date(2026, 8, 18)]

    assert daily.stale_days(pending, None) == pending
