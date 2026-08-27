# 시세 시계열(price_daily) 테이블 접근 함수

from __future__ import annotations

from collections.abc import Sequence

import psycopg

from .models import PriceDaily


def upsert_price_daily(cur: psycopg.Cursor, prices: Sequence[PriceDaily]) -> int:
    """일봉을 삽입하거나 갱신한다.

    adj_factor 는 건드리지 않는다. corporate_action 배치가 계산하는 값이라
    수집기가 재적재할 때 덮어쓰면 계산 결과가 사라진다.
    """
    if not prices:
        return 0
    cur.executemany(
        """
        INSERT INTO price_daily (
            stock_id, trade_date, open, high, low, close, volume, value
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (stock_id, trade_date) DO UPDATE SET
            open   = EXCLUDED.open,
            high   = EXCLUDED.high,
            low    = EXCLUDED.low,
            close  = EXCLUDED.close,
            volume = EXCLUDED.volume,
            value  = EXCLUDED.value
        """,
        [
            (
                p.stock_id,
                p.trade_date,
                p.open,
                p.high,
                p.low,
                p.close,
                p.volume,
                p.value,
            )
            for p in prices
        ],
    )
    return len(prices)


def known_stock_ids(cur: psycopg.Cursor) -> set[str]:
    """stock 에 있는 종목 식별자 전체. 시세의 FK 위반을 미리 걸러내는 데 쓴다."""
    cur.execute("SELECT stock_id FROM stock")
    return {row[0] for row in cur.fetchall()}
