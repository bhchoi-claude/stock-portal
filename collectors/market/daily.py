# 장 마감 후 빠진 거래일을 채우는 일 1회 배치. 지연이 길어지면 텔레그램으로 알린다

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from common.config import load_config
from common.db.conn import connect, transaction
from common.db.events import log_event
from common.notify.base import Notifier
from common.notify.telegram import TelegramNotifier

from . import delisted_refine, holidays, price_daily, stock_status
from .krx import fetch
from .price_daily_backfill import missing_dates

logger = logging.getLogger(__name__)

SEOUL = ZoneInfo("Asia/Seoul")


def is_holiday(bas_dd: str) -> bool:
    """시세가 없는 날인지 KOSPI 일별매매정보 한 번으로 확인한다.

    종목기본정보로 판정하면 안 된다. 그쪽은 휴장일에도 응답한다
    (2026-08-15 토요일에 2846건). 거래일 여부를 아는 것은 시세 API 뿐이다.

    휴장일과 '아직 공개 전' 을 구분하지 못한다. KRX 는 당일 데이터를 다음 날
    공개하므로 최근 날짜의 0건은 미공개일 수 있다. 구분은 호출부가 한다.
    """
    return not fetch("sto", price_daily.TRADE_APIS["KOSPI"], bas_dd)


def load_day(day: date) -> str:
    """하루를 채운다. 'loaded' | 'nodata' | 'failed'.

    종목 상태 갱신을 먼저 돌려야 그날 신규 상장한 종목의 시세가 FK 를 통과한다.
    이 단계가 stock 현재값 갱신도 겸한다 (stock_master 를 따로 돌리지 않는다).

    휴장일에는 돌리지 않는다. 종목기본정보는 휴장일에도 응답하므로
    그대로 돌리면 휴장일 스냅샷이 stock 에 들어간다.
    """
    bas_dd = day.strftime("%Y%m%d")
    if is_holiday(bas_dd):
        return "nodata"

    for name, entry in (("종목 상태", stock_status.main), ("일봉", price_daily.main)):
        try:
            code = entry([name, bas_dd])
        except Exception:
            # 한 수집기의 예외가 다른 날짜 처리로 번지면 안 된다
            logger.exception("%s %s 예외", day, name)
            return "failed"
        if code != 0:
            logger.error("%s %s 실패 (종료코드 %d)", day, name, code)
            return "failed"
    return "loaded"


def stale_days(pending: list[date], last_loaded: date | None) -> list[date]:
    """가진 데이터보다 뒤에 있는 미적재 거래일.

    이보다 앞의 미적재일은 휴장일이다. 공개가 날짜순으로 되므로,
    뒤 날짜가 들어왔는데 앞 날짜가 비었다면 그날은 데이터가 없는 날이다.
    """
    if last_loaded is None:
        return pending
    return [d for d in pending if d > last_loaded]


def main(argv: list[str], notifier: Notifier | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    params = load_config("collect")["daily"]
    # 수집기 CLI 다. 전략이나 피드가 아니므로 현재 시각을 직접 읽어도 된다
    today = datetime.now(SEOUL).date()

    with connect() as conn, transaction(conn) as cur:
        if len(argv) > 1:
            pending = [date.fromisoformat(argv[1])]
        else:
            start = today - timedelta(days=params["lookback_days"])
            pending = missing_dates(cur, start, today)

    logger.info("미적재 거래일 후보 %d일: %s", len(pending), pending)

    loaded: list[date] = []
    failed: list[date] = []
    for day in pending:
        result = load_day(day)
        logger.info("%s %s", day, result)
        if result == "loaded":
            loaded.append(day)
        elif result == "failed":
            failed.append(day)

    # 새 거래일이 들어온 뒤라야 폐지일과 휴장일을 다시 셀 수 있다
    if loaded:
        for name, entry in (
            ("폐지일 정밀화", delisted_refine.main),
            ("휴장일 역산", holidays.main),
        ):
            try:
                entry([name])
            except Exception:
                logger.exception("%s 예외", name)
                failed.append(today)

    with connect() as conn, transaction(conn) as cur:
        cur.execute("SELECT MAX(trade_date) FROM price_daily")
        last_loaded = cur.fetchone()[0]

    stale = stale_days(pending, last_loaded)
    # KRX 는 당일 데이터를 다음 날 공개한다. 하루 이틀 밀리는 것은 정상이다
    delayed = len(stale) > params["max_delay_days"]

    with connect() as conn, transaction(conn) as cur:
        log_event(
            cur,
            "daily",
            "ERROR" if failed or delayed else "INFO",
            "일 1회 갱신",
            category="collect",
            detail={
                "loaded": [str(d) for d in loaded],
                "failed": [str(d) for d in failed],
                "stale": [str(d) for d in stale],
                "last_loaded": str(last_loaded),
            },
        )

    if not failed and not delayed:
        print(f"{len(loaded)}일 적재. 마지막 거래일 {last_loaded}.")
        return 0

    body = f"마지막 거래일 {last_loaded}"
    if failed:
        body += f"\n실패: {', '.join(str(d) for d in failed)}"
    if delayed:
        body += f"\n미적재가 {len(stale)}일째: {', '.join(str(d) for d in stale)}"

    # 알림 설정이 없다고 원래 실패를 덮으면 안 된다. 못 보내면 로그로만 남긴다
    try:
        if notifier is None:
            notifier = TelegramNotifier.from_env()
        notifier.send("ERROR", "일 1회 갱신 이상", body)
    except RuntimeError:
        logger.exception("알림을 보내지 못했습니다")

    print(body)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
