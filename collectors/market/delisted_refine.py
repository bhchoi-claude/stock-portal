# 스냅샷으로 역산한 폐지일을 일봉의 마지막 거래일 기준으로 맞추는 CLI

from __future__ import annotations

import logging
import sys

from common.db import master
from common.db.conn import connect, transaction
from common.db.events import log_event

logger = logging.getLogger(__name__)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    with connect() as conn, transaction(conn) as cur:
        # 폐지가 아니라 마스터에서만 빠졌을 수 있다. 폐지일을 매기면 틀린다
        suspicious = master.still_trading(cur)
        updated = master.refine_delisted_at(cur)
        log_event(
            cur,
            "delisted_refine",
            "WARN" if suspicious else "INFO",
            "폐지일 정밀화",
            category="collect",
            detail={"updated": updated, "still_trading": suspicious},
        )

    if suspicious:
        logger.warning(
            "폐지로 표시됐는데 마지막 날까지 거래된 종목 %d건: %s",
            len(suspicious),
            suspicious,
        )

    print(f"폐지일 {updated}건 갱신.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
