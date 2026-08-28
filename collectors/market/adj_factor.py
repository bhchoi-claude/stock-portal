# corporate_action 에서 price_daily.adj_factor 를 다시 계산하는 CLI

from __future__ import annotations

import logging
import sys

from common.db.actions import action_stock_ids, adjusting_actions
from common.db.conn import connect, transaction
from common.db.events import log_event
from common.db.prices import apply_adj_factor, reset_adj_factor

logger = logging.getLogger(__name__)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # 되돌리고 다시 쌓는다. 증분으로 곱하면 두 번 돌릴 때마다 값이 커진다
    with connect() as conn, transaction(conn) as cur:
        stocks = action_stock_ids(cur)
        rows = reset_adj_factor(cur, stocks)
        logger.info("%d종목 %d행 조정계수 초기화", len(stocks), rows)

        actions = adjusting_actions(cur)
        applied = 0
        for stock_id, effective_date, ratio in actions:
            applied += apply_adj_factor(cur, stock_id, effective_date, ratio)

        log_event(
            cur,
            "adj_factor",
            "INFO",
            "조정계수 계산",
            category="collect",
            detail={
                "stocks": len(stocks),
                "actions": len(actions),
                "rows": applied,
            },
        )

    print(f"이벤트 {len(actions)}건으로 {applied}행의 조정계수를 갱신했습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
