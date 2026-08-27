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

    이미 트랜잭션이 열려 있으면 거부한다. psycopg 는 그 경우 세이브포인트를
    만들 뿐이라 블록을 빠져나와도 커밋되지 않는다. 커밋된 줄 알고 넘어가면
    커넥션을 닫을 때 전부 롤백된다. 2026-08-27 에 일봉 백필 204만건을 이렇게 잃었다.
    """
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        raise RuntimeError(
            "이미 트랜잭션이 열려 있습니다."
            " 커서로 직접 쿼리하지 말고 transaction() 안에서 하세요."
        )
    with conn.transaction(), conn.cursor() as cur:
        yield cur
