# 수집기 실행기가 실패를 격리하고 반복 실패에만 알리는지 확인한다

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from collectors.base import Collector, CollectResult, IndicatorRecord
from collectors.market.indicator_runner import process_name

SINCE = datetime(2026, 8, 28, tzinfo=UTC)


class FakeCollector(Collector):
    source_kind = "krx"
    source_identifier = "vkospi"
    interval_sec = 86400

    def __init__(self, result=None, boom=None):
        self._result = result
        self._boom = boom
        self.calls = 0

    def collect(self, since):
        self.calls += 1
        if self._boom is not None:
            raise self._boom
        return self._result


def record(code="VKOSPI", value="15.2"):
    return IndicatorRecord(
        indicator_code=code, period_date=date(2026, 8, 28), value=Decimal(value)
    )


class Spy:
    def __init__(self):
        self.sent = []

    def send(self, level, title, body):
        self.sent.append((level, title, body))
        return True


@pytest.fixture
def runner(monkeypatch):
    """DB 를 쓰지 않고 실행기만 확인한다."""
    from collectors.market import indicator_runner as mod

    stored = []
    failures = {"count": 0}

    monkeypatch.setattr(mod, "store", lambda c, r: stored.append((c, r)))
    monkeypatch.setattr(
        mod, "_record", lambda o, n, w, t: _fake_record(o, n, w, t, failures)
    )
    mod.stored = stored
    mod.failures = failures
    return mod


def _fake_record(outcome, notifier, window, threshold, failures):
    if outcome.success:
        return
    failures["count"] += 1
    if failures["count"] >= threshold and notifier is not None:
        notifier.send("ERROR", "수집기 반복 실패", outcome.name)


def test_한_수집기의_예외가_다른_수집기를_막지_않는다(runner):
    boom = FakeCollector(boom=RuntimeError("네트워크"))
    ok = FakeCollector(result=CollectResult(success=True, records=[record()]))

    outcomes = runner.run([boom, ok], SINCE)

    assert ok.calls == 1
    assert [o.success for o in outcomes] == [False, True]


def test_예외를_밖으로_내보내지_않는다(runner):
    outcomes = runner.run([FakeCollector(boom=RuntimeError("네트워크"))], SINCE)

    assert outcomes[0].success is False
    assert "네트워크" in outcomes[0].error


def test_성공하면_레코드를_저장한다(runner):
    ok = FakeCollector(result=CollectResult(success=True, records=[record()]))

    runner.run([ok], SINCE)

    assert len(runner.stored) == 1
    assert runner.stored[0][1] == [record()]


def test_실패하면_저장하지_않는다(runner):
    bad = FakeCollector(result=CollectResult(success=False, error="401"))

    outcomes = runner.run([bad], SINCE)

    assert runner.stored == []
    assert outcomes[0].error == "401"


def test_한_번_실패로는_알리지_않는다(runner):
    # 수집기 실패 알림은 '1시간 내 반복 시' 다 (PROJECT.md 10장)
    spy = Spy()

    runner.run([FakeCollector(boom=RuntimeError("일시 장애"))], SINCE, notifier=spy)

    assert spy.sent == []


def test_반복_실패하면_알린다(runner):
    spy = Spy()
    collectors = [FakeCollector(boom=RuntimeError("장애")) for _ in range(2)]

    runner.run(collectors, SINCE, notifier=spy)

    assert len(spy.sent) == 1
    assert spy.sent[0][0] == "ERROR"


def test_이름에_소스와_클래스가_들어간다():
    assert process_name(FakeCollector()) == "krx.FakeCollector"


def test_관찰_창은_수집기_주기에_맞춘다():
    # 하루 한 번 도는 수집기는 1시간 창에서 최대 1회만 실패한다.
    # 임계값 2 에 닿지 못해 죽은 소스가 영원히 조용해진다
    from collectors.market.indicator_runner import failure_window_hours

    assert failure_window_hours(86400, threshold=2, minimum=1) == 48
    assert failure_window_hours(3600, threshold=2, minimum=1) == 2


def test_짧은_주기에도_최소_창은_지킨다():
    from collectors.market.indicator_runner import failure_window_hours

    # 5분 주기면 창이 10분이지만 최소 1시간을 지킨다
    assert failure_window_hours(300, threshold=2, minimum=1) == 1
