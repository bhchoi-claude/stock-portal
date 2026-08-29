# heartbeat 기록. 성공·부분실패·예외가 각각 맞는 상태로 남는지 확인한다

import pytest

from common.db import heartbeat
from common.db.heartbeat import list_heartbeats, run_with_heartbeat, upsert_heartbeat


@pytest.fixture
def beats(monkeypatch):
    """_beat 을 가로채 호출 순서를 모은다. DB 없이 상태 전이만 본다."""
    recorded: list[tuple] = []

    def fake(process_name, status, *, detail=None, restart=False):
        recorded.append((process_name, status, detail, restart))

    monkeypatch.setattr(heartbeat, "_beat", fake)
    return recorded


def test_success_ends_idle(beats):
    assert run_with_heartbeat("x", lambda argv: 0, []) == 0
    assert [(b[1], b[3]) for b in beats] == [("running", True), ("idle", False)]


def test_nonzero_exit_ends_error(beats):
    """부분 실패로 1 을 돌려주는 수집기가 있다. 돌긴 돈 것과 구분한다."""
    assert run_with_heartbeat("x", lambda argv: 1, []) == 1
    assert beats[-1][1] == "error"
    assert beats[-1][2] == {"exit_code": 1}


def test_exception_ends_error_and_reraises(beats):
    def boom(argv):
        raise RuntimeError("망가짐")

    with pytest.raises(RuntimeError):
        run_with_heartbeat("x", boom, [])

    assert beats[-1][1] == "error"
    assert "RuntimeError: 망가짐" == beats[-1][2]["error"]


def test_upsert_keeps_started_at(cur):
    """실행 중 신호는 started_at 을 건드리지 않는다. restart 일 때만 다시 잡는다."""
    upsert_heartbeat(cur, "test-proc", "running", restart=True)
    started = _state(cur, "test-proc").started_at

    upsert_heartbeat(cur, "test-proc", "idle", detail={"exit_code": 0})
    after = _state(cur, "test-proc")
    assert after.started_at == started
    assert after.status == "idle"
    assert after.detail == {"exit_code": 0}
    assert after.last_beat_at >= started

    upsert_heartbeat(cur, "test-proc", "running", restart=True)
    assert _state(cur, "test-proc").started_at > started


def _state(cur, name):
    return next(s for s in list_heartbeats(cur) if s.process_name == name)
