# event_log 에 이벤트를 남기는 함수. 예외를 삼키지 않기 위한 최소 기록 경로

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import psycopg
from psycopg.types.json import Jsonb

Level = Literal["INFO", "WARN", "ERROR", "CRITICAL"]
Category = Literal["order", "collect", "regime", "system"]


def log_event(
    cur: psycopg.Cursor,
    process_name: str,
    level: Level,
    message: str,
    *,
    category: Category | None = None,
    detail: dict[str, Any] | None = None,
) -> int:
    """이벤트를 기록하고 event_id 를 돌려준다.

    표준 logging 을 대체하지 않는다. 로그 파일에 남기는 것과 별개로,
    화면과 알림에서 조회해야 하는 이벤트만 여기에 남긴다.
    """
    cur.execute(
        """
        INSERT INTO event_log (process_name, level, category, message, detail)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING event_id
        """,
        (process_name, level, category, message, Jsonb(detail) if detail else None),
    )
    return cur.fetchone()[0]


@dataclass(frozen=True)
class EventRow:
    """event_log 한 행."""

    event_id: int
    process_name: str
    level: str
    category: str | None
    message: str
    detail: dict[str, Any] | None
    created_at: datetime


def recent_events(
    cur: psycopg.Cursor, *, levels: Sequence[str] | None = None, limit: int
) -> list[EventRow]:
    """최근 이벤트. levels 를 주면 그 등급만 본다."""
    cur.execute(
        """
        SELECT event_id, process_name, level, category, message, detail, created_at
        FROM event_log
        WHERE %s::text[] IS NULL OR level = ANY(%s)
        ORDER BY created_at DESC, event_id DESC
        LIMIT %s
        """,
        (list(levels) if levels else None, list(levels) if levels else None, limit),
    )
    return [EventRow(*row) for row in cur.fetchall()]
