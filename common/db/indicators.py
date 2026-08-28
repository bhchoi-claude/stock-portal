# 시장분석 지표(indicator, indicator_value) 테이블 접근 함수

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg


def upsert_indicator_values(cur: psycopg.Cursor, records: Sequence[Any]) -> int:
    """지표값을 삽입하거나 갱신한다. change_rate 는 따로 계산한다."""
    if not records:
        return 0
    cur.executemany(
        """
        INSERT INTO indicator_value (indicator_code, period_date, value)
        VALUES (%s, %s, %s)
        ON CONFLICT (indicator_code, period_date) DO UPDATE SET
            value        = EXCLUDED.value,
            collected_at = NOW()
        """,
        [(r.indicator_code, r.period_date, r.value) for r in records],
    )
    return len(records)


def recompute_change_rate(cur: psycopg.Cursor, indicator_code: str) -> int:
    """전기 대비 변화율을 다시 계산한다.

    한 지표의 시계열 전체를 다시 센다. 값이 나중에 정정돼도 뒤따르는
    변화율이 함께 맞는다. 표가 작아 전체를 도는 편이 단순하다.
    """
    cur.execute(
        """
        WITH ranked AS (
            SELECT indicator_code, period_date, value,
                   LAG(value) OVER (
                       PARTITION BY indicator_code ORDER BY period_date
                   ) AS prev
            FROM indicator_value
            WHERE indicator_code = %s
        )
        UPDATE indicator_value v
        SET change_rate = CASE
            WHEN r.prev IS NOT NULL AND r.prev <> 0
            THEN (r.value - r.prev) / ABS(r.prev)
        END
        FROM ranked r
        WHERE v.indicator_code = r.indicator_code
          AND v.period_date = r.period_date
        """,
        (indicator_code,),
    )
    return cur.rowcount


def active_indicators(cur: psycopg.Cursor, *, regime_only: bool = False) -> list[str]:
    """활성 지표 코드. regime_only 면 판정에 쓰는 것만."""
    cur.execute(
        "SELECT indicator_code FROM indicator"
        " WHERE is_active AND (NOT %s OR use_in_regime)"
        " ORDER BY indicator_code",
        (regime_only,),
    )
    return [row[0] for row in cur.fetchall()]


def touch_source(cur: psycopg.Cursor, kind: str, identifier: str) -> int:
    """수집 성공 시각을 남긴다. 등록되지 않은 소스면 아무것도 하지 않는다."""
    cur.execute(
        "UPDATE source SET last_success_at = NOW()"
        " WHERE kind = %s AND identifier = %s",
        (kind, identifier),
    )
    return cur.rowcount


def recent_failures(cur: psycopg.Cursor, process_name: str, within_hours: int) -> int:
    """최근 실패 횟수. 반복 실패 알림 판단에 쓴다."""
    cur.execute(
        """
        SELECT COUNT(*) FROM event_log
        WHERE process_name = %s
          AND level = 'ERROR'
          AND created_at > NOW() - make_interval(hours => %s)
        """,
        (process_name, within_hours),
    )
    return cur.fetchone()[0]
