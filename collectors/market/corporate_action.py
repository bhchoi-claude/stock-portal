# 상장주식수 변화에서 조정 이벤트를 찾아 corporate_action 에 적재하는 CLI

from __future__ import annotations

import logging
import sys
from datetime import date
from decimal import Decimal

from common.config import load_config
from common.db.actions import upsert_corporate_actions
from common.db.conn import connect, transaction
from common.db.events import log_event
from common.db.models import CorporateAction, make_stock_id
from common.db.prices import price_jumps, traded_dates

from .krx import fetch
from .price_daily import TRADE_APIS

logger = logging.getLogger(__name__)

SOURCE = "krx"

# 상장주식수가 줄면 감자·액면병합이다. 둘 다 가격을 조정해야 한다.
# SCHEMA.md 의 action_type 에 감자가 없어 병합과 한 갈래로 묶는다 (승인 사항).
# 실제 구분은 detail 의 상장주식수로 남긴다
MERGE = "merge"


def shares_on(bas_dd: str) -> dict[str, int]:
    """그날 세 시장의 종목별 상장주식수."""
    shares: dict[str, int] = {}
    for api_id in TRADE_APIS.values():
        for row in fetch("sto", api_id, bas_dd):
            shares[make_stock_id("KRX", row["ISU_CD"])] = int(row["LIST_SHRS"])
    return shares


def detect_actions(
    day: date,
    before: dict[str, int],
    after: dict[str, int],
    jumped: set[str],
) -> tuple[list[CorporateAction], list[str]]:
    """상장주식수가 바뀌고 가격도 점프한 종목을 찾는다.

    두 신호가 모두 있어야 한다.

    - 주식수 변화만 보면 자기주식 소각·전환사채 전환이 섞인다.
      주식수는 줄지만 가격은 조정되지 않는다 (2024-11-08 KRX:264450, -3%)
    - 가격 점프만 보면 거래정지 해제가 섞인다.
      주식수가 그대로인데 가격만 크게 움직인다 (2024-07-18 KRX:065560)

    `jumped` 는 그날 가격제한폭 밖으로 움직인 종목이다.
    비율은 가격이 아니라 주식수에서 얻는다. 가격에는 시장 변동이 섞여 있다.

    늘어난 경우는 무상증자와 유상증자가 섞여 있어 `adjusts_price` 가 갈린다.
    구분할 근거가 없으므로 적재하지 않고 목록만 돌려준다.
    """
    actions = []
    increased = []

    for stock_id, now in after.items():
        was = before.get(stock_id)
        if was is None or was == 0 or was == now:
            continue
        if stock_id not in jumped:
            continue
        if now > was:
            increased.append(stock_id)
            continue
        actions.append(
            CorporateAction(
                stock_id=stock_id,
                effective_date=day,
                action_type=MERGE,
                adjusts_price=True,
                ratio=Decimal(now) / Decimal(was),
                source=SOURCE,
                detail={"shares_before": was, "shares_after": now},
            )
        )
    return actions, increased


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    params = load_config("collect")["corporate_action"]
    with connect() as conn, transaction(conn) as cur:
        days = sorted(traded_dates(cur))
        jumps = price_jumps(
            cur,
            Decimal(str(params["price_jump_low"])),
            Decimal(str(params["price_jump_high"])),
        )
    logger.info("가격제한폭 초과 %d건", len(jumps))
    if len(days) < 2:
        print("일봉이 부족해 비교할 수 없습니다.")
        return 1

    if len(argv) > 2:
        first, last = date.fromisoformat(argv[1]), date.fromisoformat(argv[2])
        days = [d for d in days if first <= d <= last]

    logger.info("거래일 %d일 (%s ~ %s)", len(days), days[0], days[-1])

    before = shares_on(days[0].strftime("%Y%m%d"))
    total = 0
    increased_all: list[tuple[date, str]] = []

    for index, day in enumerate(days[1:], 2):
        after = shares_on(day.strftime("%Y%m%d"))
        jumped = {sid for sid, d in jumps if d == day}
        actions, increased = detect_actions(day, before, after, jumped)
        increased_all += [(day, sid) for sid in increased]

        if actions:
            with connect() as conn, transaction(conn) as cur:
                upsert_corporate_actions(cur, actions)
            total += len(actions)
            for a in actions:
                logger.info(
                    "%s %s 주식수 %d -> %d (비율 %s)",
                    day,
                    a.stock_id,
                    a.detail["shares_before"],
                    a.detail["shares_after"],
                    a.ratio.quantize(Decimal("0.000001")),
                )
        if increased:
            logger.info(
                "%s 주식수 증가 %d건 (보류): %s", day, len(increased), increased[:5]
            )
        if index % 50 == 0:
            logger.info("%d/%d 진행, 누적 %d건", index, len(days), total)

        before = after

    with connect() as conn, transaction(conn) as cur:
        log_event(
            cur,
            "corporate_action",
            "INFO",
            "조정 이벤트 적재",
            category="collect",
            detail={
                "first": str(days[0]),
                "last": str(days[-1]),
                "merged": total,
                "increased": len(increased_all),
            },
        )

    print(f"조정 이벤트 {total}건 적재. 주식수 증가 {len(increased_all)}건은 보류.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
