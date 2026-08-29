# 조정 후 시계열이 끊긴 곳을 세는 점검 CLI. 적재하지 않고 보기만 한다

from __future__ import annotations

import logging
import sys
from decimal import Decimal

from common.config import load_config
from common.db.conn import connect, transaction
from common.db.prices import adj_discontinuities

logger = logging.getLogger(__name__)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    band = load_config("collect")["corporate_action"]
    params = load_config("collect")["adj_check"]

    with connect() as conn, transaction(conn) as cur:
        rows = adj_discontinuities(
            cur,
            Decimal(str(band["price_jump_low"])),
            Decimal(str(band["price_jump_high"])),
            params["liquidation_days"],
        )

    broken = [r for r in rows if r[4] == 0]
    spanning = [r for r in rows if r[4] > 0]

    for stock_id, prev_date, trade_date, ratio, halt in broken:
        logger.info("%s %s -> %s 비율 %s", stock_id, prev_date, trade_date, ratio)
    for stock_id, prev_date, trade_date, ratio, halt in spanning:
        logger.info(
            "%s %s -> %s 비율 %s (정지 %d일)",
            stock_id,
            prev_date,
            trade_date,
            ratio,
            halt,
        )

    print(
        f"연속 거래일 {len(broken)}건 / {len({r[0] for r in broken})}종목,"
        f" 정지 구간 포함 {len(spanning)}건 / {len({r[0] for r in spanning})}종목."
        " 정리매매는 제외했다."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
