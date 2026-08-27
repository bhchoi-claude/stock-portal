# 장 마감 후 시장 데이터를 갱신하는 일 1회 배치. 실패하면 텔레그램으로 알린다

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from common.db.conn import connect, transaction
from common.db.events import log_event
from common.notify.base import Notifier
from common.notify.telegram import TelegramNotifier

from . import EXIT_HOLIDAY, delisted_refine, price_daily, stock_master
from .krx import fetch

logger = logging.getLogger(__name__)

SEOUL = ZoneInfo("Asia/Seoul")

# 순서가 중요하다. 신규 상장 종목이 stock 에 있어야 그 종목의 시세가 FK 를
# 통과하고, 폐지일 정밀화는 그날 일봉이 들어온 뒤라야 맞는 값을 본다
STEPS: tuple[tuple[str, Callable[[list[str]], int]], ...] = (
    ("종목 마스터", stock_master.main),
    ("일봉", price_daily.main),
    ("폐지일 정밀화", delisted_refine.main),
)


def is_holiday(bas_dd: str) -> bool:
    """거래일이 아닌지 KOSPI 일별매매정보 한 번으로 확인한다.

    종목기본정보로 판정하면 안 된다. 그쪽은 휴장일에도 응답한다
    (2026-08-15 토요일에 2846건). 거래일 여부를 아는 것은 시세 API 뿐이다.

    단계보다 먼저 봐야 한다. 종목 마스터가 먼저 돌면 휴장일 스냅샷이
    stock 과 stock_status 에 그대로 들어간다.
    """
    return not fetch("sto", price_daily.TRADE_APIS["KOSPI"], bas_dd)


def run_steps(bas_dd: str) -> tuple[bool, list[str]]:
    """단계를 순서대로 돌린다. (휴장일 여부, 실패한 단계 이름) 을 돌려준다.

    한 단계가 실패해도 다음을 진행한다. 각 단계가 자기 트랜잭션을 갖고
    다시 돌려도 안전하므로, 뒤 단계를 막는 것보다 최대한 받는 쪽이 낫다.
    """
    failed: list[str] = []

    for name, entry in STEPS:
        logger.info("%s 시작", name)
        try:
            code = entry([name, bas_dd])
        except Exception:
            # 한 수집기의 예외가 다른 수집기로 번지면 안 된다
            logger.exception("%s 예외", name)
            failed.append(name)
            continue

        if code == EXIT_HOLIDAY:
            logger.info("%s 이 휴장일입니다. 배치를 종료합니다", bas_dd)
            return True, failed
        if code != 0:
            logger.error("%s 실패 (종료코드 %d)", name, code)
            failed.append(name)

    return False, failed


def main(argv: list[str], notifier: Notifier | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    # 수집기 CLI 다. 전략이나 피드가 아니므로 현재 시각을 직접 읽어도 된다
    bas_dd = argv[1] if len(argv) > 1 else datetime.now(SEOUL).strftime("%Y%m%d")

    if is_holiday(bas_dd):
        holiday, failed = True, []
        logger.info("%s 은 거래일이 아닙니다. 아무것도 갱신하지 않습니다", bas_dd)
    else:
        holiday, failed = run_steps(bas_dd)

    with connect() as conn, transaction(conn) as cur:
        log_event(
            cur,
            "daily",
            "ERROR" if failed else "INFO",
            "휴장일" if holiday else "일 1회 갱신",
            category="collect",
            detail={"bas_dd": bas_dd, "holiday": holiday, "failed": failed},
        )

    if not failed:
        print(f"{bas_dd} {'휴장일입니다' if holiday else '갱신 완료'}.")
        return 0

    # 알림 설정이 없다고 원래 실패를 덮으면 안 된다. 못 보내면 로그로만 남긴다.
    # Notifier.send 는 발송 실패 시 예외 대신 False 를 돌려준다
    try:
        if notifier is None:
            notifier = TelegramNotifier.from_env()
        notifier.send(
            "ERROR", "일 1회 갱신 실패", f"{bas_dd}\n실패: {', '.join(failed)}"
        )
    except RuntimeError:
        logger.exception("알림을 보내지 못했습니다")

    print(f"{bas_dd} 갱신 실패: {', '.join(failed)}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
