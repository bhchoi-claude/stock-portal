# raw_message(수집 원문)와 텔레그램 소스 조회 함수

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

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


@dataclass(frozen=True)
class StoredMessage:
    """적재된 원문 하나. 분석 대상이다."""

    message_id: int
    content: str


def unanalyzed(cur: psycopg.Cursor, limit: int) -> list[StoredMessage]:
    """아직 분석하지 않은 원문. 오래된 것부터 본다."""
    cur.execute(
        "SELECT message_id, content FROM raw_message"
        " WHERE analyzed_at IS NULL ORDER BY published_at LIMIT %s",
        (limit,),
    )
    return [StoredMessage(*row) for row in cur.fetchall()]


def mark_analyzed(
    cur: psycopg.Cursor,
    message_ids: Sequence[int],
    method: str,
    *,
    filtered: bool = False,
) -> int:
    """분석을 끝냈다고 표시한다. filtered 면 규칙 필터에서 제외된 것이다."""
    if not message_ids:
        return 0
    cur.execute(
        "UPDATE raw_message SET analyzed_at = NOW(), analysis_method = %s,"
        " is_filtered = %s WHERE message_id = ANY(%s)",
        (method, filtered, list(message_ids)),
    )
    return cur.rowcount


def insert_stock_mentions(cur: psycopg.Cursor, pairs: Sequence[tuple[int, str]]) -> int:
    """(message_id, stock_id) 를 적재한다. 같은 쌍은 무시한다."""
    if not pairs:
        return 0
    cur.executemany(
        "INSERT INTO stock_mention (message_id, stock_id) VALUES (%s, %s)"
        " ON CONFLICT DO NOTHING",
        list(pairs),
        returning=False,
    )
    return cur.rowcount


@dataclass(frozen=True)
class MessageView:
    """원문 조회 결과 한 줄."""

    message_id: int
    source_name: str
    content: str
    published_at: datetime


def messages_for_keyword(
    cur: psycopg.Cursor, term: str, since: datetime, limit: int
) -> list[MessageView]:
    """그 키워드가 나온 원문. 동의어로 묶인 것도 함께 나온다."""
    cur.execute(
        """
        SELECT r.message_id, s.name, r.content, r.published_at
        FROM keyword_mention m
        JOIN raw_message r USING (message_id)
        JOIN source s ON s.source_id = r.source_id
        WHERE m.keyword_id = (
                SELECT COALESCE(canonical_id, keyword_id) FROM keyword WHERE term = %s
              )
          AND r.published_at >= %s
        ORDER BY r.published_at DESC
        LIMIT %s
        """,
        (term, since, limit),
    )
    return [MessageView(*row) for row in cur.fetchall()]


def collection_status(cur: psycopg.Cursor) -> list[tuple[str, int, datetime | None]]:
    """채널별 수집 현황. (이름, 건수, 마지막 글 시각)"""
    cur.execute(
        """
        SELECT s.name, COUNT(r.message_id), MAX(r.published_at)
        FROM source s LEFT JOIN raw_message r USING (source_id)
        WHERE s.kind = 'telegram' AND s.is_active
        GROUP BY s.name ORDER BY 2 DESC
        """
    )
    return [(row[0], row[1], row[2]) for row in cur.fetchall()]


def samples_on_day(
    cur: psycopg.Cursor, keyword_id: int, day: date, limit: int
) -> list[str]:
    """그 키워드가 그날 나온 원문 본문. 브리핑에 표본으로 넣는다.

    날짜 경계는 `aggregate_day` 와 같은 달력일(KST) 기준이다. 다르게 잡으면
    브리핑의 표본이 집계된 건수와 어긋난다.
    """
    cur.execute(
        """
        SELECT r.content
        FROM keyword_mention m
        JOIN raw_message r USING (message_id)
        WHERE m.keyword_id = %s
          AND (r.published_at AT TIME ZONE 'Asia/Seoul')::date = %s
        ORDER BY r.published_at DESC
        LIMIT %s
        """,
        (keyword_id, day, limit),
    )
    return [row[0] for row in cur.fetchall()]


def analyzed_count(cur: psycopg.Cursor, day: date) -> int:
    """그날 분석을 마친 원문 수. 브리핑이 얼마나 두꺼운 근거 위에 있는지다."""
    cur.execute(
        """
        SELECT COUNT(*) FROM raw_message
        WHERE analyzed_at IS NOT NULL
          AND (published_at AT TIME ZONE 'Asia/Seoul')::date = %s
        """,
        (day,),
    )
    return cur.fetchone()[0]
