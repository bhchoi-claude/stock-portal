# 과거 종목 스냅샷을 되짚어 폐지 종목까지 stock 에 채우는 백필 CLI

from __future__ import annotations

import logging
import sys
from dataclasses import replace
from datetime import date, timedelta

from common.config import load_config
from common.db import master
from common.db.conn import connect, transaction
from common.db.events import log_event
from common.db.models import Stock

from .stock_master import collect

logger = logging.getLogger(__name__)


class SnapshotShrank(RuntimeError):
    """직전 스냅샷보다 종목 수가 크게 줄었다.

    API 이상 응답을 '전 종목이 폐지됐다' 로 읽으면 stock 전체가 망가진다.
    """


def snapshot_dates(start: date, end: date, interval_days: int) -> list[date]:
    """start 부터 간격을 두고, 마지막에 end 를 포함한 날짜 목록을 만든다."""
    dates = []
    cursor = start
    while cursor < end:
        dates.append(cursor)
        cursor += timedelta(days=interval_days)
    dates.append(end)
    return dates


def snapshot(target: date, shift_days: int) -> tuple[date, list[Stock]]:
    """휴장일이면 0건이 오므로 데이터가 나올 때까지 하루씩 민다."""
    for offset in range(shift_days + 1):
        day = target + timedelta(days=offset)
        stocks = collect(day.strftime("%Y%m%d"))
        if stocks:
            return day, stocks
    return target, []


def with_delisted(
    latest: dict[str, Stock], last_seen: dict[str, date], final_day: date
) -> list[Stock]:
    """마지막 스냅샷에 없는 종목에 폐지일을 채운다.

    실제 폐지일은 마지막으로 관측된 날과 그 다음 스냅샷 사이에 있다.
    존재가 마지막으로 확인된 날의 다음 날로 둔다. 스냅샷 간격만큼 오차가 있다.
    """
    return [
        stock
        if last_seen[stock_id] == final_day
        else replace(stock, delisted_at=last_seen[stock_id] + timedelta(days=1))
        for stock_id, stock in latest.items()
    ]


def gather(
    dates: list[date], shift_days: int, max_shrink_ratio: float
) -> tuple[dict[str, Stock], dict[str, date], date | None]:
    """스냅샷을 시간순으로 돌며 종목의 최종 상태와 마지막 관측일을 모은다."""
    latest: dict[str, Stock] = {}
    last_seen: dict[str, date] = {}
    previous = 0
    final_day: date | None = None

    for target in dates:
        day, stocks = snapshot(target, shift_days)
        if not stocks:
            logger.warning("%s 전후로 데이터가 없어 건너뜁니다", target)
            continue

        if previous and len(stocks) < previous * (1 - max_shrink_ratio):
            raise SnapshotShrank(
                f"{day} 스냅샷이 {len(stocks)}건입니다."
                f" 직전 {previous}건 대비 급감했습니다."
            )

        for stock in stocks:
            latest[stock.stock_id] = stock
            last_seen[stock.stock_id] = day

        previous = len(stocks)
        final_day = day
        logger.info("%s 스냅샷 %d건, 누적 %d종목", day, len(stocks), len(latest))

    return latest, last_seen, final_day


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if len(argv) < 3:
        print("사용법: python -m collectors.market.stock_master_backfill 시작 종료")
        print("  날짜는 YYYYMMDD. 예: 20230829 20260826")
        return 2

    params = load_config("collect")["stock_master_backfill"]
    start = date.fromisoformat(argv[1])
    end = date.fromisoformat(argv[2])

    dates = snapshot_dates(start, end, params["snapshot_interval_days"])
    logger.info("%s ~ %s, 스냅샷 %d회", start, end, len(dates))

    latest, last_seen, final_day = gather(
        dates, params["holiday_shift_days"], params["max_shrink_ratio"]
    )
    if final_day is None:
        print("스냅샷을 하나도 받지 못했습니다.")
        return 1

    stocks = with_delisted(latest, last_seen, final_day)
    delisted = sum(1 for s in stocks if s.delisted_at)

    with connect() as conn, transaction(conn) as cur:
        master.upsert_stocks(cur, stocks)
        log_event(
            cur,
            "stock_master_backfill",
            "INFO",
            "과거 종목 마스터 백필",
            category="collect",
            detail={
                "start": str(start),
                "end": str(final_day),
                "snapshots": len(dates),
                "count": len(stocks),
                "delisted": delisted,
            },
        )
        total = master.count_stocks(cur, listed_only=False)

    print(f"{len(stocks)}종목 적재 (폐지 {delisted}건). stock 총 {total}건.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
