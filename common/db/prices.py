# 시세 시계열(price_daily) 테이블 접근 함수

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

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


def traded_range(cur: psycopg.Cursor) -> tuple[date, date] | None:
    """일봉이 있는 구간. 없으면 None."""
    cur.execute("SELECT MIN(trade_date), MAX(trade_date) FROM price_daily")
    first, last = cur.fetchone()
    return (first, last) if first else None


def traded_dates(cur: psycopg.Cursor) -> set[date]:
    """일봉이 있는 거래일 전체. 휴장일 역산의 기준이다."""
    cur.execute("SELECT DISTINCT trade_date FROM price_daily")
    return {row[0] for row in cur.fetchall()}


def price_jumps(
    cur: psycopg.Cursor, low: Decimal, high: Decimal
) -> set[tuple[str, date]]:
    """전일 종가 대비 가격제한폭 밖으로 움직인 (종목, 거래일).

    권리락일에는 조정 후 기준가로 제한폭이 적용되므로, 기계적 조정은 반드시
    이 범위를 벗어난다. 자기주식 소각·전환사채 전환은 벗어나지 않는다.
    """
    cur.execute(
        """
        WITH d AS (
            SELECT stock_id, trade_date, close,
                   LAG(close) OVER (PARTITION BY stock_id ORDER BY trade_date) prev
            FROM price_daily
        )
        SELECT stock_id, trade_date FROM d
        WHERE prev > 0 AND (close / prev < %s OR close / prev > %s)
        """,
        (low, high),
    )
    return set(cur.fetchall())
