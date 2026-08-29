# 키움에서 투자자별 순매수를 받아 trading_flow 에 적재하는 CLI

from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

from common.broker.errors import BrokerError
from common.broker.kiwoom import KiwoomBroker
from common.config import load_config
from common.db.conn import connect, transaction
from common.db.events import log_event
from common.db.heartbeat import run_with_heartbeat
from common.db.prices import listed_stock_ids, upsert_trading_flow

logger = logging.getLogger(__name__)

SEOUL = ZoneInfo("Asia/Seoul")


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    params = load_config("collect")["trading_flow"]
    # 수집기 CLI 다. 전략이나 피드가 아니므로 현재 시각을 직접 읽어도 된다
    end = date.fromisoformat(argv[1]) if len(argv) > 1 else datetime.now(SEOUL).date()

    with connect() as conn, transaction(conn) as cur:
        universe = listed_stock_ids(cur)
    if not universe:
        print("상장 종목이 없습니다.")
        return 1

    # 한 번에 100 거래일이 오므로 매일 돌리면 과거분도 함께 메워진다
    logger.info("%d종목, %s 까지", len(universe), end)

    broker = KiwoomBroker(is_paper=params["use_paper"])
    stored = 0
    failed: list[str] = []

    for index, stock_id in enumerate(universe, 1):
        try:
            flows = broker.get_investor_flow(stock_id, end)
        except BrokerError:
            # 한 종목의 실패가 나머지를 막으면 안 된다
            logger.exception("%s 수급 조회 실패", stock_id)
            failed.append(stock_id)
            continue

        with connect() as conn, transaction(conn) as cur:
            stored += upsert_trading_flow(cur, flows)

        if index % 50 == 0:
            logger.info("%d/%d 진행, 누적 %d행", index, len(universe), stored)

    with connect() as conn, transaction(conn) as cur:
        log_event(
            cur,
            "trading_flow",
            "WARN" if failed else "INFO",
            "수급 적재",
            category="collect",
            detail={"universe": len(universe), "rows": stored, "failed": failed},
        )

    # 전 종목을 돌면 거래정지·신규상장 등으로 몇 건은 늘 실패한다.
    # 한 건에 실패로 끝내면 매일 알림이 울린다
    ratio = len(failed) / len(universe)
    too_many = ratio > params["max_failure_ratio"]
    if failed:
        logger.warning(
            "실패 %d종목 (%.1f%%): %s", len(failed), ratio * 100, failed[:10]
        )

    print(
        f"{len(universe)}종목에서 {stored}행 적재,"
        f" {len(failed)}종목 실패 ({ratio * 100:.1f}%)."
    )
    return 1 if too_many else 0


if __name__ == "__main__":
    sys.exit(run_with_heartbeat("trading_flow", main, sys.argv))
