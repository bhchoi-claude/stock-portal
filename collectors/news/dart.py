# DART 공시 목록을 dart_disclosure 에 적재하는 배치. LLM 을 쓰지 않는다

from __future__ import annotations

import logging
import sys
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from collectors.market.dart import disclosures
from common.broker.errors import BrokerError
from common.config import load_config
from common.db.conn import connect, transaction
from common.db.disclosures import Disclosure, upsert_disclosures
from common.db.events import log_event
from common.db.heartbeat import run_with_heartbeat
from common.db.master import listed_boards
from common.db.models import make_stock_id

logger = logging.getLogger(__name__)

PROCESS = "dart"
SEOUL = ZoneInfo("Asia/Seoul")

VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="


def to_disclosure(row: dict[str, str], kind: str, known: set[str]) -> Disclosure | None:
    """응답 한 줄을 적재 형식으로. 접수번호가 없으면 버린다.

    `rcept_dt` 는 날짜뿐이라 그날 0시(KST)로 둔다. 목록 API 가 시각을 주지
    않는다. 공시가 몇 시에 올라왔는지는 이 표로 알 수 없다.
    """
    rcept_no = (row.get("rcept_no") or "").strip()
    rcept_dt = (row.get("rcept_dt") or "").strip()
    if not rcept_no or len(rcept_dt) != 8:
        return None

    code = (row.get("stock_code") or "").strip()
    stock_id = make_stock_id("KRX", code) if code else None

    return Disclosure(
        rcept_no=rcept_no,
        # 우리 종목 표에 없으면 비워 둔다. 외래키가 걸려 있다
        stock_id=stock_id if stock_id in known else None,
        corp_name=(row.get("corp_name") or "").strip(),
        report_name=" ".join((row.get("report_nm") or "").split()),
        disclosure_type=kind,
        submitted_at=datetime.combine(
            date(int(rcept_dt[:4]), int(rcept_dt[4:6]), int(rcept_dt[6:])),
            time.min,
            tzinfo=SEOUL,
        ).astimezone(UTC),
        url=VIEWER + rcept_no,
    )


def collect(params: dict[str, Any], known: set[str]) -> tuple[list[Disclosure], int]:
    """설정한 유형·시장을 돌며 공시를 모은다. (행 목록, 실패 횟수)"""
    today = datetime.now(SEOUL).date()
    bgn = (today - timedelta(days=params["lookback_days"])).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    rows: dict[str, Disclosure] = {}
    failures = 0

    for kind in params["types"]:
        for market in params["markets"]:
            page = 1
            while page <= params["max_pages"]:
                try:
                    found, total = disclosures(
                        bgn, end, kind, market, page, params["page_size"]
                    )
                except BrokerError:
                    # 한 유형의 실패가 나머지를 막으면 안 된다
                    logger.exception("공시 조회 실패 (%s/%s %d쪽)", kind, market, page)
                    failures += 1
                    break

                for row in found:
                    record = to_disclosure(row, kind, known)
                    if record is not None:
                        rows[record.rcept_no] = record

                if page >= total:
                    break
                page += 1

    return list(rows.values()), failures


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    params = load_config("collect")["dart"]

    with connect() as conn, transaction(conn) as cur:
        known = set(listed_boards(cur))

    rows, failures = collect(params, known)

    with connect() as conn, transaction(conn) as cur:
        stored = upsert_disclosures(cur, rows)
        log_event(
            cur,
            PROCESS,
            "WARN" if failures else "INFO",
            "공시 적재",
            category="collect",
            detail={
                "disclosures": len(rows),
                "stored": stored,
                "failures": failures,
                "unmatched": sum(1 for row in rows if row.stock_id is None),
            },
        )

    print(f"공시 {len(rows)}건 적재, 조회 실패 {failures}건.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run_with_heartbeat(PROCESS, main, sys.argv))
