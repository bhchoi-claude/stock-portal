# 키워드 빈도를 집계하고 급등을 알리는 배치

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from common.config import load_config
from common.db.conn import connect, transaction
from common.db.events import log_event
from common.db.heartbeat import run_with_heartbeat
from common.db.keywords import (
    Surge,
    aggregate_day,
    alerted_terms,
    refresh_surge,
    surging,
)
from common.notify.telegram import TelegramNotifier

logger = logging.getLogger(__name__)

PROCESS = "news_surge"

# 집계는 달력일(KST) 기준이다. 거래일이 아니라 주말·휴장일에도 행이 생긴다
SEOUL = ZoneInfo("Asia/Seoul")


def days_to_rebuild(argv: list[str], today: date, rebuild_days: int) -> list[date]:
    """다시 집계할 날짜. 인자로 시작일을 주면 그날부터 오늘까지 전부 한다."""
    if len(argv) > 1:
        start = date.fromisoformat(argv[1])
        span = (today - start).days + 1
        return [start + timedelta(days=offset) for offset in range(max(span, 1))]
    return sorted(today - timedelta(days=offset) for offset in range(rebuild_days))


def describe(surge: Surge) -> str:
    if surge.is_new:
        return f"{surge.term} {surge.mention_count}회 (처음 등장)"
    return (
        f"{surge.term} {surge.mention_count}회"
        f" (평소 {surge.ma7:.1f}회, {surge.surge_ratio:.1f}배)"
    )


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    params: dict[str, Any] = load_config("collect")["news"]["surge"]
    today = datetime.now(SEOUL).date()
    days = days_to_rebuild(argv, today, params["rebuild_days"])

    with connect() as conn, transaction(conn) as cur:
        rows = 0
        for day in days:
            rows += aggregate_day(cur, day)
            refresh_surge(cur, day)

        candidates = surging(
            cur,
            today,
            min_ratio=Decimal(str(params["min_ratio"])),
            min_baseline=Decimal(str(params["min_baseline"])),
            new_min_count=params["new_min_count"],
        )
        already = alerted_terms(cur, PROCESS, today)
        fresh = [surge for surge in candidates if surge.term not in already]

        for surge in fresh:
            log_event(
                cur,
                PROCESS,
                "INFO",
                "급등 키워드",
                category="collect",
                detail={
                    "date": str(today),
                    "term": surge.term,
                    "count": surge.mention_count,
                    "ma7": str(surge.ma7),
                    "ratio": str(surge.surge_ratio),
                },
            )

    if fresh:
        _notify(today, fresh)

    print(
        f"{days[0]} ~ {days[-1]} {rows}행 집계."
        f" 급등 후보 {len(candidates)}종, 새로 알린 것 {len(fresh)}종."
    )
    return 0


def _notify(day: date, fresh: list[Surge]) -> None:
    """한 번에 묶어 보낸다. 종목마다 따로 보내면 알림이 도배된다."""
    try:
        notifier = TelegramNotifier.from_env()
    except RuntimeError:
        logger.exception("알림 설정이 없어 급등을 알리지 못합니다")
        return
    notifier.send("INFO", "급등 키워드", f"{day}\n" + "\n".join(map(describe, fresh)))


if __name__ == "__main__":
    sys.exit(run_with_heartbeat(PROCESS, main, sys.argv))
