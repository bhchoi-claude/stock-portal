# 알림 발송기의 공통 인터페이스

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

Level = Literal["INFO", "WARN", "ERROR", "CRITICAL"]


class Notifier(ABC):
    @abstractmethod
    def send(self, level: Level, title: str, body: str) -> bool:
        """발송 성공 여부를 돌려준다.

        예외를 밖으로 던지지 않는다. 알림 실패가 엔진이나 수집기를 멈추면
        안 되기 때문이다. 실패한 이유는 구현체가 로그로 남긴다.
        """
