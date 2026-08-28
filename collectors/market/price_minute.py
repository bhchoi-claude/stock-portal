# 키움에서 관심종목 분봉을 받아 price_minute 에 적재하는 CLI

from __future__ import annotations

import logging
import sys

from common.broker.errors import BrokerError
from common.broker.kiwoom import KiwoomBroker
from common.config import load_config
from common.db.conn import connect, transaction
from common.db.events import log_event
from common.db.prices import top_by_value, upsert_price_minute

logger = logging.getLogger(__name__)


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    params = load_config("collect")["price_minute"]

    with connect() as conn, transaction(conn) as cur:
        universe = top_by_value(cur, params["universe_size"])
    if not universe:
        print("일봉이 없어 관심종목을 고를 수 없습니다.")
        return 1

    logger.info("관심종목 %d개", len(universe))

    # 시세 조회는 계좌와 무관하다. 실전 앱키가 없으므로 모의로 받는다
    broker = KiwoomBroker(is_paper=params["use_paper"])

    stored = 0
    failed: list[str] = []

    for index, stock_id in enumerate(universe, 1):
        try:
            candles = broker.get_candles(stock_id, params["interval"], params["count"])
        except BrokerError:
            # 한 종목의 실패가 나머지를 막으면 안 된다
            logger.exception("%s 분봉 조회 실패", stock_id)
            failed.append(stock_id)
            continue

        with connect() as conn, transaction(conn) as cur:
            stored += upsert_price_minute(cur, candles)

        if index % 50 == 0:
            logger.info("%d/%d 진행, 누적 %d봉", index, len(universe), stored)

    with connect() as conn, transaction(conn) as cur:
        log_event(
            cur,
            "price_minute",
            "WARN" if failed else "INFO",
            "분봉 적재",
            category="collect",
            detail={
                "universe": len(universe),
                "candles": stored,
                "failed": failed,
            },
        )

    if failed:
        logger.warning("실패 %d종목: %s", len(failed), failed[:10])

    print(f"{len(universe)}종목에서 {stored}봉 적재, {len(failed)}종목 실패.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
