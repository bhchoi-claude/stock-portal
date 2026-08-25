# PostgreSQL 커넥션과 트랜잭션 범위를 제공하는 DB 접근 진입점

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from ..env import require_env


def load_database_url() -> str:
    """DATABASE_URL 을 읽는다. 없으면 RuntimeError."""
    return require_env("DATABASE_URL")


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
