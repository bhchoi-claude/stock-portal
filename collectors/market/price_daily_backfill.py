# 기간을 끊어 일봉을 채우는 백필 CLI. 중단해도 다시 돌리면 이어서 받는다

from __future__ import annotations

import logging
import sys
from datetime import date, timedelta

import psycopg

from common.db.conn import connect, transaction
from common.db.events import log_event
from common.db.prices import known_stock_ids, upsert_price_daily

from .price_daily import collect, drop_unknown

logger = logging.getLogger(__name__)


def missing_dates(cur: psycopg.Cursor, start: date, end: date) -> list[date]:
    """아직 price_daily 에 없는 평일을 고른다.

    이미 받은 날을 다시 부르지 않으므로 중단 후 재개가 그냥 된다.
    주말은 호출하지 않는다. 공휴일은 불러 봐야 알 수 있지만 토·일은 확실하다.
    """
    cur.execute(
        "SELECT DISTINCT trade_date FROM price_daily WHERE trade_date BETWEEN %s AND %s",
        (start, end),
    )
    done = {row[0] for row in cur.fetchall()}

    days = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5 and cursor not in done:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def load_day(conn: psycopg.Connection, day: date, known: set[str]) -> int | None:
    """하루치를 적재하고 건수를 돌려준다. 휴장일이면 None."""
    prices = collect(day.strftime("%Y%m%d"))
    if not prices:
        return None

    kept, skipped = drop_unknown(prices, known)
    with transaction(conn) as cur:
        upsert_price_daily(cur, kept)
    if skipped:
        logger.info("%s 건너뜀 %d건", day, len(skipped))
    return len(kept)


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    if len(argv) < 3:
        print("사용법: python -m collectors.market.price_daily_backfill 시작 종료")
        print("  날짜는 YYYYMMDD. 예: 20230829 20260826")
        return 2

    start = date.fromisoformat(argv[1])
    end = date.fromisoformat(argv[2])

    with connect() as conn:
        # 읽기도 transaction() 안에서 한다. 커서로 바로 쿼리하면 암묵적 트랜잭션이
        # 열린 채 남고, 이후 transaction() 이 세이브포인트가 되어 커밋되지 않는다
        with transaction(conn) as cur:
            known = known_stock_ids(cur)
            days = missing_dates(cur, start, end)
        logger.info(
            "%s ~ %s, 받을 날 %d일, stock %d종목", start, end, len(days), len(known)
        )

        loaded = holidays = 0
        failed: list[date] = []

        for index, day in enumerate(days, 1):
            try:
                count = load_day(conn, day, known)
            except Exception:
                # 하루가 실패해도 나머지는 받는다. 다시 돌리면 이 날만 다시 시도한다
                logger.exception("%s 적재 실패", day)
                failed.append(day)
                continue

            if count is None:
                holidays += 1
                logger.info("%s 휴장일 (%d/%d)", day, index, len(days))
            else:
                loaded += count
                logger.info("%s %d건 (%d/%d)", day, count, index, len(days))

        with transaction(conn) as cur:
            log_event(
                cur,
                "price_daily_backfill",
                "WARN" if failed else "INFO",
                "일봉 백필",
                category="collect",
                detail={
                    "start": str(start),
                    "end": str(end),
                    "days": len(days),
                    "rows": loaded,
                    "holidays": holidays,
                    "failed": [str(d) for d in failed],
                },
            )

    print(f"{loaded}건 적재, 휴장일 {holidays}일, 실패 {len(failed)}일.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
