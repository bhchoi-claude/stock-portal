# raw_message(수집 원문)와 텔레그램 소스 조회 함수

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import psycopg


@dataclass(frozen=True)
class TelegramSource:
    """수집 대상 채널 하나. identifier 는 텔레그램 채널 숫자 ID 다."""

    source_id: int
    channel_id: int
    name: str


@dataclass(frozen=True)
class RawMessage:
    source_id: int
    external_id: str
    content: str
    content_hash: str
    published_at: datetime  # UTC


def telegram_sources(cur: psycopg.Cursor) -> list[TelegramSource]:
    """활성 텔레그램 채널. 숫자가 아닌 identifier 는 건너뛴다."""
    cur.execute(
        "SELECT source_id, identifier, name FROM source"
        " WHERE kind = 'telegram' AND is_active ORDER BY source_id"
    )
    return [
        TelegramSource(row[0], int(row[1]), row[2])
        for row in cur.fetchall()
        if row[1].lstrip("-").isdigit()
    ]


def last_external_id(cur: psycopg.Cursor, source_id: int) -> int | None:
    """마지막으로 받은 메시지 번호. 재시작 후 여기서부터 따라잡는다.

    external_id 는 TEXT 이지만 텔레그램 메시지 번호는 채널 안에서 증가하는
    정수다. 정수로 비교해야 10 이 9 보다 뒤라는 것이 맞다.
    """
    cur.execute(
        "SELECT MAX(external_id::bigint) FROM raw_message WHERE source_id = %s",
        (source_id,),
    )
    return cur.fetchone()[0]


def insert_messages(cur: psycopg.Cursor, records: Sequence[RawMessage]) -> int:
    """원문을 적재한다. 같은 소스의 같은 내용은 무시한다.

    실제로 들어간 건수를 돌려준다. 중복은 건수에 세지 않는다.
    """
    if not records:
        return 0
    cur.executemany(
        """
        INSERT INTO raw_message
            (source_id, external_id, content, content_hash, published_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (source_id, content_hash) DO NOTHING
        """,
        [
            (r.source_id, r.external_id, r.content, r.content_hash, r.published_at)
            for r in records
        ],
        returning=False,
    )
    return cur.rowcount
