# stock_filter 테이블 접근. 제외·허용 목록이다 (SCHEMA.md 5장)

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import psycopg

# 'all' 은 전략을 가리지 않는다. 조회할 때 항상 함께 본다
ALL = "all"

TYPES: tuple[str, ...] = ("block", "allow")


@dataclass(frozen=True)
class FilterRow:
    """stock_filter 한 행. 화면이 종목명까지 함께 보여준다."""

    filter_id: int
    stock_id: str
    name: str | None
    strategy: str
    filter_type: str
    reason: str | None
    until_date: date | None
    created_at: datetime


def blocked_stock_ids(cur: psycopg.Cursor, strategy: str, day: date) -> set[str]:
    """오늘 사면 안 되는 종목. **진입에만 쓴다.**

    청산은 이 목록을 보지 않는다. 이미 들고 있는 종목을 제외 목록에 넣었다고
    팔지 못하면 갇힌다.

    `until_date` 가 NULL 이면 무기한이고, 지난 것은 자연히 빠진다. 행을
    지우지 않는 것은 '언제 왜 막았는가' 가 기록으로 남아야 하기 때문이다.
    """
    cur.execute(
        "SELECT stock_id FROM stock_filter"
        " WHERE filter_type = 'block' AND strategy IN (%s, %s)"
        "   AND (until_date IS NULL OR until_date >= %s)",
        (strategy, ALL, day),
    )
    return {row[0] for row in cur.fetchall()}


def list_filters(cur: psycopg.Cursor) -> list[FilterRow]:
    """목록 전체. 만료된 것도 준다 — 화면이 지난 것임을 표시한다."""
    cur.execute(
        "SELECT f.filter_id, f.stock_id, s.name, f.strategy, f.filter_type,"
        " f.reason, f.until_date, f.created_at"
        " FROM stock_filter f LEFT JOIN stock s ON s.stock_id = f.stock_id"
        " ORDER BY f.created_at DESC, f.filter_id DESC"
    )
    return [FilterRow(*row) for row in cur.fetchall()]


def add_filter(
    cur: psycopg.Cursor,
    *,
    stock_id: str,
    strategy: str,
    filter_type: str,
    reason: str | None = None,
    until_date: date | None = None,
) -> int:
    """목록에 한 건 넣는다. 같은 종목이 여러 번 들어갈 수 있다.

    UNIQUE 를 걸지 않는 것은 스키마가 그렇기 때문이다. 사유와 기간이 다른
    기록이 겹쳐도 조회는 `OR` 라 결과가 같다.
    """
    cur.execute(
        "INSERT INTO stock_filter (stock_id, strategy, filter_type, reason, until_date)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING filter_id",
        (stock_id, strategy, filter_type, reason, until_date),
    )
    row = cur.fetchone()
    assert row is not None  # RETURNING 이 있으므로 항상 한 행이다
    return int(row[0])


def remove_filter(cur: psycopg.Cursor, filter_id: int) -> bool:
    """지운다. 없던 것이면 거짓."""
    cur.execute("DELETE FROM stock_filter WHERE filter_id = %s", (filter_id,))
    return cur.rowcount > 0
