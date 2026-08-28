# corporate_action(분할·병합·증자 등 조정 이벤트) 테이블 접근 함수

from __future__ import annotations

from collections.abc import Sequence

import psycopg
from psycopg.types.json import Jsonb

from .models import CorporateAction


def upsert_corporate_actions(
    cur: psycopg.Cursor, actions: Sequence[CorporateAction]
) -> int:
    """조정 이벤트를 삽입하거나 갱신한다.

    같은 종목·같은 날·같은 종류는 한 건이다. 다시 돌려도 늘어나지 않는다.
    """
    if not actions:
        return 0
    cur.executemany(
        """
        INSERT INTO corporate_action (
            stock_id, effective_date, action_type, ratio, adjusts_price, source, detail
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (stock_id, effective_date, action_type) DO UPDATE SET
            ratio         = EXCLUDED.ratio,
            adjusts_price = EXCLUDED.adjusts_price,
            source        = EXCLUDED.source,
            detail        = EXCLUDED.detail
        """,
        [
            (
                a.stock_id,
                a.effective_date,
                a.action_type,
                a.ratio,
                a.adjusts_price,
                a.source,
                Jsonb(a.detail) if a.detail else None,
            )
            for a in actions
        ],
    )
    return len(actions)


def adjusting_actions(cur: psycopg.Cursor) -> list[tuple[str, object, object]]:
    """가격 조정 대상인 이벤트. 조정계수 계산이 쓴다."""
    cur.execute(
        "SELECT stock_id, effective_date, ratio FROM corporate_action"
        " WHERE adjusts_price AND ratio > 0 ORDER BY stock_id, effective_date"
    )
    return cur.fetchall()


def action_stock_ids(cur: psycopg.Cursor) -> list[str]:
    """조정 이벤트가 있는 종목. 조정계수를 되돌릴 범위다."""
    cur.execute("SELECT DISTINCT stock_id FROM corporate_action")
    return [row[0] for row in cur.fetchall()]
