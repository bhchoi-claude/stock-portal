# .env 와 환경변수에서 설정값을 읽는다. 비밀값을 다루는 유일한 진입점

from __future__ import annotations

import functools
import os
import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


@functools.cache
def _dotenv() -> dict[str, str]:
    """.env 를 한 번만 읽어 캐시한다. 값이 바뀌면 프로세스를 재시작한다.

    값에 # 가 들어갈 수 있으므로 줄 중간의 # 는 주석으로 보지 않는다.
    """
    path = PROJECT_ROOT / ".env"
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def load_env(key: str, default: str | None = None) -> str | None:
    """환경변수를 먼저 보고, 없으면 .env 에서 읽는다.

    붙여넣기로 섞여든 공백·따옴표를 항상 걷어낸다. grep 으로는 값이 있어
    보이지만 실제로는 깨져 있는 경우를 2026-08-25 에 겪었다.
    """
    value = os.environ.get(key)
    if value is None:
        value = _dotenv().get(key)
    if value is None:
        return default

    value = value.strip()
    return value or default


def require_env(key: str) -> str:
    """값이 없으면 RuntimeError. 비밀값이므로 예외 메시지에 값을 담지 않는다."""
    value = load_env(key)
    if value is None:
        raise RuntimeError(f"{key} 설정이 없습니다. 환경변수나 .env 에 넣으세요.")
    return value
