# price_minute 월 파티션을 미리 만들어 두는 CLI

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime

from common.config import load_config
from common.db.conn import connect, transaction
from common.db.events import log_event
from common.db.prices import create_minute_partition, existing_minute_partitions

logger = logging.getLogger(__name__)


def months_from(start: datetime, count: int) -> list[datetime]:
    """start 가 속한 달부터 count 개월의 1일 목록. 전부 UTC 다.

    파티션 경계가 UTC 이므로 여기서도 UTC 로 센다. 현지 시각으로 세면
    월말 자정 근처에서 한 달이 어긋난다.
    """
    months = []
    year, month = start.year, start.month
    for _ in range(count):
        months.append(datetime(year, month, 1, tzinfo=UTC))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    params = load_config("collect")["partitions"]
    # 수집기 CLI 다. 전략이나 피드가 아니므로 현재 시각을 직접 읽어도 된다
    wanted = months_from(datetime.now(UTC), params["months_ahead"])

    with connect() as conn, transaction(conn) as cur:
        existing = existing_minute_partitions(cur)
        created = [
            create_minute_partition(cur, month)
            for month in wanted
            if f"price_minute_{month:%Y%m}" not in existing
        ]
        if created:
            log_event(
                cur,
                "partitions",
                "INFO",
                "분봉 파티션 생성",
                category="system",
                detail={"created": created},
            )

    if created:
        logger.info("파티션 %d개 생성: %s", len(created), created)

    last = f"price_minute_{wanted[-1]:%Y%m}"
    print(f"파티션 {len(created)}개 생성. {last} 까지 확보했습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
