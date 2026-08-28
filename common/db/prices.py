# 시세 시계열(price_daily) 테이블 접근 함수

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import psycopg.sql

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


def reset_adj_factor(cur: psycopg.Cursor, stock_ids: Sequence[str]) -> int:
    """조정계수를 1 로 되돌린다. 다시 계산하기 전에 부른다."""
    if not stock_ids:
        return 0
    cur.execute(
        "UPDATE price_daily SET adj_factor = 1 WHERE stock_id = ANY(%s)",
        (list(stock_ids),),
    )
    return cur.rowcount


def apply_adj_factor(
    cur: psycopg.Cursor, stock_id: str, effective_date: date, ratio: Decimal
) -> int:
    """이벤트 이전 가격의 조정계수에 비율의 역수를 곱한다.

    이벤트 당일 가격은 이미 조정된 값이므로 건드리지 않는다.
    50:1 감자(ratio 0.02)면 그 전 가격에 50 을 곱해야 이어진다.

    이벤트마다 한 번씩 부르면 누적곱이 된다. 순서는 상관없다.
    """
    cur.execute(
        "UPDATE price_daily SET adj_factor = adj_factor / %s"
        " WHERE stock_id = %s AND trade_date < %s",
        (ratio, stock_id, effective_date),
    )
    return cur.rowcount


def top_by_value(cur: psycopg.Cursor, limit: int) -> list[str]:
    """가장 최근 거래일의 거래대금 상위 종목.

    단타 대상은 유동성이 있어야 한다. 별도의 관심종목 표를 두지 않고
    시세에서 뽑는다. 폐지 종목은 최근 거래일에 없으므로 자연히 빠진다.
    """
    cur.execute(
        """
        SELECT stock_id FROM price_daily
        WHERE trade_date = (SELECT MAX(trade_date) FROM price_daily)
        ORDER BY value DESC NULLS LAST
        LIMIT %s
        """,
        (limit,),
    )
    return [row[0] for row in cur.fetchall()]


def upsert_price_minute(cur: psycopg.Cursor, candles: Sequence[Any]) -> int:
    """분봉을 삽입하거나 갱신한다.

    분봉에는 조정계수가 없다 (SCHEMA.md 2장). 원주가를 그대로 담는다.
    """
    if not candles:
        return 0
    cur.executemany(
        """
        INSERT INTO price_minute (stock_id, ts, open, high, low, close, volume)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (stock_id, ts) DO UPDATE SET
            open   = EXCLUDED.open,
            high   = EXCLUDED.high,
            low    = EXCLUDED.low,
            close  = EXCLUDED.close,
            volume = EXCLUDED.volume
        """,
        [(c.stock_id, c.ts, c.open, c.high, c.low, c.close, c.volume) for c in candles],
    )
    return len(candles)


def upsert_trading_flow(cur: psycopg.Cursor, flows: Sequence[Any]) -> int:
    """투자자별 순매수를 삽입하거나 갱신한다."""
    if not flows:
        return 0
    cur.executemany(
        """
        INSERT INTO trading_flow (
            stock_id, trade_date, foreign_net, institution_net, individual_net
        )
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (stock_id, trade_date) DO UPDATE SET
            foreign_net     = EXCLUDED.foreign_net,
            institution_net = EXCLUDED.institution_net,
            individual_net  = EXCLUDED.individual_net
        """,
        [
            (
                f.stock_id,
                f.trade_date,
                f.foreign_net,
                f.institution_net,
                f.individual_net,
            )
            for f in flows
        ],
    )
    return len(flows)


def existing_minute_partitions(cur: psycopg.Cursor) -> set[str]:
    """이미 있는 price_minute 월 파티션 이름."""
    cur.execute(
        "SELECT relname FROM pg_class"
        " WHERE relname LIKE 'price_minute_%%' AND relkind = 'r'"
    )
    return {row[0] for row in cur.fetchall()}


def create_minute_partition(cur: psycopg.Cursor, month: datetime) -> str:
    """한 달치 파티션을 만든다. 경계는 UTC 다.

    세션 타임존에 기대면 경계가 9시간 어긋난다. 마이그레이션이 오프셋을
    명시한 것과 같은 이유다.
    """
    nxt = (month + timedelta(days=32)).replace(day=1)
    name = f"price_minute_{month:%Y%m}"
    cur.execute(
        psycopg.sql.SQL(
            "CREATE TABLE {} PARTITION OF price_minute FOR VALUES FROM (%s) TO (%s)"
        ).format(psycopg.sql.Identifier(name)),
        (month, nxt),
    )
    return name
