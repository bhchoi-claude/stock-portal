# 일봉에서 휴장일을 역산해 exchange_holiday 에 적재하는 CLI

from __future__ import annotations

import logging
import sys
from datetime import date, timedelta

from common.db import master
from common.db.conn import connect, transaction
from common.db.events import log_event
from common.db.models import Holiday
from common.db.prices import traded_dates, traded_range

logger = logging.getLogger(__name__)

EXCHANGE = "KRX"


def missing_weekdays(first: date, last: date, traded: set[date]) -> list[date]:
    """구간 안에서 거래가 없던 평일. 이것이 휴장일이다.

    주말은 담지 않는다. 토·일은 변하지 않는 규칙이라 데이터로 남길 이유가 없고,
    두 곳에 같은 지식을 두면 어긋난다. 조회하는 쪽이 요일을 함께 본다.
    """
    days = []
    cursor = first
    while cursor <= last:
        if cursor.weekday() < 5 and cursor not in traded:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    with connect() as conn, transaction(conn) as cur:
        span = traded_range(cur)
        if span is None:
            print("일봉이 없어 역산할 수 없습니다.")
            return 1

        # 일봉이 있는 구간 안에서만 역산한다. 구간 밖의 공백은 '아직 안 받은 날'
        # 이지 휴장일이 아니다. 특히 최근 며칠은 KRX 가 아직 공개하지 않은 것이다
        first, last = span
        holidays = missing_weekdays(first, last, traded_dates(cur))

        # 이름은 역산으로 알 수 없다. upsert 가 기존 이름을 지우지 않는다
        master.upsert_holidays(
            cur, [Holiday(exchange=EXCHANGE, holiday_date=d) for d in holidays]
        )
        log_event(
            cur,
            "holidays",
            "INFO",
            "휴장일 역산 적재",
            category="collect",
            detail={"first": str(first), "last": str(last), "count": len(holidays)},
        )

    logger.info("%s ~ %s 구간에서 휴장일 %d일", first, last, len(holidays))
    print(f"휴장일 {len(holidays)}일 적재 ({first} ~ {last}).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
