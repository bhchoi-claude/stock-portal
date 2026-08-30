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


def adj_discontinuities(
    cur: psycopg.Cursor, low: Decimal, high: Decimal, liquidation_days: int
) -> list[tuple[str, date, date, Decimal, int]]:
    """조정 후 시계열이 끊긴 곳. (종목, 직전 거래일, 거래일, 비율, 정지일수).

    조정 대상 이벤트를 가진 종목만 본다. 조정계수가 붙지 않는 종목의
    가격 급변은 여기서 볼 것이 아니다.

    세 가지를 걸러야 실제 결함만 남는다 (2026-08-29 실측으로 갈랐다).

    - **거래량 0 인 날을 뺀다.** KRX 는 거래정지 기간에도 행을 주는데
      마지막 종가를 그대로 반복한다. 체결된 값이 아니다
    - **정리매매를 뺀다.** 가격제한폭이 없어 하루 -80% 가 정상이다
    - **정지 구간을 사이에 둔 것을 표시한다.** 정지 전후의 두 값은 둘 다
      실제 체결가지만 며칠이 비어 있다. 연속 거래일의 급변과 같이 셀 수 없다

    `halt_days` 가 0 이면 연속된 두 거래일이다. 그것만이 곧바로 결함이다.
    """
    cur.execute(
        """
        WITH target AS (
            SELECT DISTINCT stock_id FROM corporate_action WHERE adjusts_price
        ),
        traded AS (
            SELECT p.stock_id, p.trade_date, p.close * p.adj_factor px
            FROM price_daily p JOIN target USING (stock_id)
            WHERE p.volume > 0
        ),
        d AS (
            SELECT stock_id, trade_date, px,
                   LAG(px) OVER w prev, LAG(trade_date) OVER w prev_date
            FROM traded WINDOW w AS (PARTITION BY stock_id ORDER BY trade_date)
        )
        SELECT d.stock_id, d.prev_date, d.trade_date, ROUND(d.px / d.prev, 4),
               (SELECT COUNT(*) FROM price_daily h
                WHERE h.stock_id = d.stock_id AND h.volume = 0
                  AND h.trade_date > d.prev_date AND h.trade_date < d.trade_date)
        FROM d JOIN stock s USING (stock_id)
        WHERE d.prev > 0 AND (d.px / d.prev < %s OR d.px / d.prev > %s)
          AND NOT (s.delisted_at IS NOT NULL
                   AND s.delisted_at - d.trade_date BETWEEN 0 AND %s)
        ORDER BY d.trade_date
        """,
        (low, high, liquidation_days),
    )
    return cur.fetchall()


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
    # DDL 에는 파라미터를 쓸 수 없다. 경계를 리터럴로 박아 넣는다
    cur.execute(
        psycopg.sql.SQL(
            "CREATE TABLE {} PARTITION OF price_minute FOR VALUES FROM ({}) TO ({})"
        ).format(
            psycopg.sql.Identifier(name),
            psycopg.sql.Literal(month),
            psycopg.sql.Literal(nxt),
        )
    )
    return name


def listed_stock_ids(cur: psycopg.Cursor) -> list[str]:
    """상장중인 종목 전체. 수급은 시장 전체를 받아야 지표가 시장을 대표한다."""
    cur.execute(
        "SELECT stock_id FROM stock WHERE delisted_at IS NULL ORDER BY stock_id"
    )
    return [row[0] for row in cur.fetchall()]


def foreign_net_by_date(
    cur: psycopg.Cursor, min_stocks: int
) -> list[tuple[date, Decimal]]:
    """거래일별 외국인 순매수 합계.

    종목 수가 `min_stocks` 에 못 미치는 날은 뺀다. 일부만 수집된 날을 합치면
    시장 전체 수급이 아니라 '그날 받은 것들의 합' 이 된다. 그 값이 지표로
    들어가면 다른 날과 비교할 수 없다.
    """
    cur.execute(
        """
        SELECT trade_date, SUM(foreign_net)
        FROM trading_flow
        WHERE foreign_net IS NOT NULL
        GROUP BY trade_date
        HAVING COUNT(*) >= %s
        ORDER BY trade_date
        """,
        (min_stocks,),
    )
    return [(row[0], row[1]) for row in cur.fetchall()]


def flow_coverage(cur: psycopg.Cursor) -> list[tuple[date, int]]:
    """거래일별 수급 수집 종목 수. 커버리지 확인용."""
    cur.execute(
        "SELECT trade_date, COUNT(*) FROM trading_flow"
        " GROUP BY trade_date ORDER BY trade_date"
    )
    return [(row[0], row[1]) for row in cur.fetchall()]


def candles_until(
    cur: psycopg.Cursor, stock_id: str, as_of: date, count: int
) -> list[tuple[date, Decimal, Decimal, Decimal, Decimal, int]]:
    """기준일까지의 일봉을 오래된 순서로. **조정가로 준다** (INTERFACES.md 3.3).

    조정하지 않으면 분할일이 급락으로 보여 손절이 대량 발동한다.
    거래량도 함께 조정한다. 분할하면 거래량이 늘어나기 때문이다.

    기준일 이후 행은 나오지 않는다. 백테스트의 미래 참조를 막는 지점이다.
    """
    cur.execute(
        """
        SELECT trade_date,
               open  * adj_factor, high   * adj_factor,
               low   * adj_factor, close  * adj_factor,
               (volume / adj_factor)::bigint
        FROM price_daily
        WHERE stock_id = %s AND trade_date <= %s
        ORDER BY trade_date DESC
        LIMIT %s
        """,
        (stock_id, as_of, count),
    )
    return list(reversed(cur.fetchall()))


def raw_close(
    cur: psycopg.Cursor, stock_id: str, as_of: date
) -> tuple[date, Decimal, int] | None:
    """기준일까지의 마지막 종가. **조정하지 않은 원주가다.**

    현재가는 조정 대상이 아니다 (INTERFACES.md 3.3).
    """
    cur.execute(
        "SELECT trade_date, close, volume FROM price_daily"
        " WHERE stock_id = %s AND trade_date <= %s"
        " ORDER BY trade_date DESC LIMIT 1",
        (stock_id, as_of),
    )
    return cur.fetchone()


def open_on(cur: psycopg.Cursor, stock_id: str, day: date) -> Decimal | None:
    """그날의 시가(원주가). 체결 시뮬레이터가 쓴다."""
    cur.execute(
        "SELECT open FROM price_daily WHERE stock_id = %s AND trade_date = %s",
        (stock_id, day),
    )
    row = cur.fetchone()
    return row[0] if row else None


def universe_at(cur: psycopg.Cursor, day: date, limit: int) -> list[str]:
    """그날 거래된 종목 중 매매 대상. 거래대금 상위 순서.

    **`stock` 의 현재값이 아니라 `stock_status` 의 그날 값을 본다**
    (SCHEMA.md 1장). 현재값으로 과거를 판단하면 '오늘 관리종목인 회사를
    2년 전 백테스트에서도 제외' 하는 미래 참조가 된다.

    폐지 종목도 폐지 이전에는 포함된다. 빼면 생존편향이 생긴다.
    """
    cur.execute(
        """
        SELECT p.stock_id
        FROM price_daily p
        JOIN stock s USING (stock_id)
        WHERE p.trade_date = %s
          AND NOT s.is_preferred AND NOT s.is_spac
          AND NOT EXISTS (
              SELECT 1 FROM stock_status st
              WHERE st.stock_id = p.stock_id
                AND st.valid_from <= %s
                AND (st.valid_to IS NULL OR st.valid_to > %s)
                AND (st.is_managed OR st.is_suspended)
          )
        ORDER BY p.value DESC NULLS LAST, p.stock_id
        LIMIT %s
        """,
        (day, day, day, limit),
    )
    return [row[0] for row in cur.fetchall()]


def board_at(cur: psycopg.Cursor, stock_id: str, day: date) -> str | None:
    """그날의 시장 구분. 증권거래세율이 시장마다 다를 수 있다."""
    cur.execute(
        "SELECT board FROM stock_status"
        " WHERE stock_id = %s AND valid_from <= %s"
        "   AND (valid_to IS NULL OR valid_to > %s)"
        " ORDER BY valid_from DESC LIMIT 1",
        (stock_id, day, day),
    )
    row = cur.fetchone()
    return row[0] if row else None


def last_traded_on_or_before(
    cur: psycopg.Cursor, stock_id: str, day: date
) -> date | None:
    """폐지 종목의 마지막 거래일. 정리매매 마지막 가격으로 청산할 때 쓴다."""
    cur.execute(
        "SELECT MAX(trade_date) FROM price_daily"
        " WHERE stock_id = %s AND trade_date <= %s",
        (stock_id, day),
    )
    return cur.fetchone()[0]
