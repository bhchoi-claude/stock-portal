# 브로커 에러 분류. INTERFACES.md 2.3 규격이다

from __future__ import annotations


class BrokerError(Exception):
    """브로커 호출이 실패했다."""


class TransientError(BrokerError):
    """다시 걸면 될 수 있다. 타임아웃, 일시 장애."""


class PermanentError(BrokerError):
    """다시 걸어도 소용없다. 잔고부족, 잘못된 종목, 권한 없음.

    401 은 여기다. 인증 실패는 재시도로 풀리지 않는다.
    """


class RateLimitError(TransientError):
    """호출 한도를 넘었다."""

    def __init__(self, message: str, retry_after: float = 1.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after
