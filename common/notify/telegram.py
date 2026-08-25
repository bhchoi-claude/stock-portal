# 텔레그램 봇 API 로 알림을 보내는 Notifier 구현체

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from ..env import require_env
from .base import Level, Notifier

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"


class TelegramNotifier(Notifier):
    def __init__(self, token: str, chat_id: str, *, timeout: float = 10.0) -> None:
        self._token = token
        self._chat_id = chat_id
        self._timeout = timeout

    @classmethod
    def from_env(cls) -> TelegramNotifier:
        return cls(require_env("TELEGRAM_BOT_TOKEN"), require_env("TELEGRAM_CHAT_ID"))

    def send(self, level: Level, title: str, body: str) -> bool:
        payload = {"chat_id": self._chat_id, "text": format_message(level, title, body)}
        request = urllib.request.Request(
            f"{API_BASE}/bot{self._token}/sendMessage",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            logger.error("텔레그램 발송 실패 (HTTP %s): %s", exc.code, _describe(exc))
            return False
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            # URL 에 토큰이 들어 있으므로 예외를 그대로 찍지 않는다
            logger.error("텔레그램 발송 실패: %s", type(exc).__name__)
            return False

        # HTTP 200 이어도 본문의 ok 를 봐야 한다 (키움에서 같은 함정을 겪었다)
        if not result.get("ok"):
            logger.error("텔레그램이 거부했습니다: %s", result.get("description"))
            return False
        return True


def format_message(level: Level, title: str, body: str) -> str:
    """서식 없는 평문으로 만든다.

    Markdown 을 쓰면 종목명이나 본문의 _ * [ 를 매번 이스케이프해야 하고,
    빠뜨리면 발송 자체가 400 으로 실패한다. 알림은 전달이 우선이다.
    """
    return f"[{level}] {title}\n{body}" if body else f"[{level}] {title}"


def _describe(exc: urllib.error.HTTPError) -> str:
    """에러 본문에서 description 만 뽑는다. 토큰이 섞인 URL 은 남기지 않는다."""
    try:
        return str(json.loads(exc.read().decode("utf-8")).get("description"))
    except (ValueError, OSError):
        return "본문 없음"
