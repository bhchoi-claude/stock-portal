# dart_disclosure(전자공시) 테이블 접근 함수

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import psycopg


@dataclass(frozen=True)
class Disclosure:
    rcept_no: str
    stock_id: str | None
    corp_name: str
    report_name: str
    disclosure_type: str | None
    submitted_at: datetime
    url: str | None


def upsert_disclosures(cur: psycopg.Cursor, rows: Sequence[Disclosure]) -> int:
    """접수번호 기준으로 적재한다. 같은 공시를 다시 받아도 늘지 않는다."""
    if not rows:
        return 0
    cur.executemany(
        """
        INSERT INTO dart_disclosure (
            rcept_no, stock_id, corp_name, report_name,
            disclosure_type, submitted_at, url
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (rcept_no) DO UPDATE SET
            stock_id        = EXCLUDED.stock_id,
            report_name     = EXCLUDED.report_name,
            disclosure_type = EXCLUDED.disclosure_type
        """,
        [
            (
                row.rcept_no,
                row.stock_id,
                row.corp_name,
                row.report_name,
                row.disclosure_type,
                row.submitted_at,
                row.url,
            )
            for row in rows
        ],
        returning=False,
    )
    return cur.rowcount


def recent_disclosures(cur: psycopg.Cursor, limit: int) -> list[Disclosure]:
    """최근 공시. 화면에서 본다."""
    cur.execute(
        "SELECT rcept_no, stock_id, corp_name, report_name, disclosure_type,"
        " submitted_at, url FROM dart_disclosure"
        " ORDER BY submitted_at DESC, rcept_no DESC LIMIT %s",
        (limit,),
    )
    return [Disclosure(*row) for row in cur.fetchall()]
