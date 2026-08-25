# 텔레그램 발송기의 응답 판정과 토큰 노출 여부를 확인한다. 실제로 발송하지 않는다

import json
import logging
import urllib.error

import pytest

from common.notify.telegram import TelegramNotifier, format_message

TOKEN = "123456:AAaaBBbbCCccDDdd"


class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


@pytest.fixture
def notifier():
    return TelegramNotifier(TOKEN, "-100123")


def patch_urlopen(monkeypatch, result, captured=None):
    def fake_urlopen(request, timeout=None):
        if captured is not None:
            captured.append(request)
        if isinstance(result, Exception):
            raise result
        return FakeResponse(result)

    monkeypatch.setattr("common.notify.telegram.urllib.request.urlopen", fake_urlopen)


def test_발송_성공(notifier, monkeypatch):
    captured = []
    patch_urlopen(monkeypatch, {"ok": True, "result": {}}, captured)

    assert notifier.send("INFO", "제목", "본문") is True

    body = json.loads(captured[0].data.decode("utf-8"))
    assert body == {"chat_id": "-100123", "text": "[INFO] 제목\n본문"}


def test_http_200_이어도_ok_가_false_면_실패다(notifier, monkeypatch):
    # 키움에서 겪은 것과 같은 함정이다. 상태코드만 보면 실패를 성공으로 읽는다
    patch_urlopen(monkeypatch, {"ok": False, "description": "chat not found"})

    assert notifier.send("ERROR", "제목", "본문") is False


def test_네트워크_오류는_예외를_던지지_않는다(notifier, monkeypatch):
    # 알림 실패가 엔진을 멈추면 안 된다
    patch_urlopen(monkeypatch, urllib.error.URLError("연결 실패"))

    assert notifier.send("CRITICAL", "엔진 중단", "") is False


def test_로그에_토큰이_남지_않는다(notifier, monkeypatch, caplog):
    patch_urlopen(monkeypatch, urllib.error.URLError(f"failed to reach /bot{TOKEN}/"))

    with caplog.at_level(logging.ERROR):
        notifier.send("ERROR", "제목", "본문")

    assert caplog.records, "실패했으면 로그가 남아야 한다"
    assert TOKEN not in caplog.text


def test_본문이_비면_제목만_보낸다():
    assert format_message("INFO", "제목", "") == "[INFO] 제목"
