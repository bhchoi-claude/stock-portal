# PostgreSQL 커넥션과 트랜잭션 범위를 제공하는 DB 접근 진입점

from __future__ import annotations

import os
import pathlib
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]


def load_database_url() -> str:
    """환경변수를 먼저 보고, 없으면 프로젝트 루트의 .env 에서 읽는다."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url.strip()

    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip("\"'")

    raise RuntimeError("DATABASE_URL 이 없습니다. 환경변수나 .env 에 설정하세요.")


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """커넥션을 열고 블록을 벗어나면 닫는다. 커밋 범위는 transaction() 이 정한다."""
    conn = psycopg.connect(load_database_url())
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: psycopg.Connection) -> Iterator[psycopg.Cursor]:
    """블록이 정상 종료하면 커밋, 예외가 나면 롤백한다.

    호출부가 트랜잭션 범위를 명시하도록 커서를 직접 만들어 쓰지 않는다.
    """
    with conn.transaction(), conn.cursor() as cur:
        yield cur
