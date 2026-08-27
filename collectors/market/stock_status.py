# 종목 마스터를 하루치 받아 상장·폐지·이전상장을 감지하고 이력을 남기는 CLI

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from common.config import load_config
from common.db import master
from common.db.conn import connect, transaction
from common.db.events import log_event
from common.db.models import Stock, StockStatus

from .stock_master import collect
from .stock_master_backfill import SnapshotShrank

logger = logging.getLogger(__name__)

SEOUL = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class Changes:
    listed: list[str]
    delisted: list[str]
    moved: list[str]
    relisted: list[str]


def detect(incoming: list[Stock], known: dict[str, str], delisted: set[str]) -> Changes:
    """이번 스냅샷과 상장중인 종목을 견줘 변경을 찾는다.

    `known` 은 상장중인 종목의 시장 구분이다. 폐지 종목은 들어 있지 않다.
    폐지는 '이전 적재에 있었는데 이번에 없는 것' 이다 (checklist 승인 사항).
    """
    seen = {s.stock_id: s.board for s in incoming}
    appeared = seen.keys() - known.keys()

    return Changes(
        # 폐지로 표시됐던 종목이 다시 나타난 것은 신규 상장이 아니다
        listed=sorted(appeared - delisted),
        delisted=sorted(known.keys() - seen.keys()),
        moved=sorted(
            sid for sid, board in seen.items() if known.get(sid, board) != board
        ),
        relisted=sorted(appeared & delisted),
    )


def apply(cur, day: date, incoming: list[Stock], changes: Changes) -> None:
    """감지한 변경을 stock(현재값)과 stock_status(이력)에 함께 쓴다."""
    master.upsert_stocks(cur, incoming)

    # 이전상장은 이력을 끊고 새 시장으로 다시 연다. 끊어야 open_stock_status 가 연다
    master.close_stock_status(cur, changes.moved, day)

    # 폐지는 이력을 끊고 폐지일을 적는다. 행은 지우지 않는다
    master.close_stock_status(cur, changes.delisted, day)
    master.mark_delisted(cur, changes.delisted, day)

    # 다시 나타난 종목은 폐지 표시를 지운다. upsert 는 COALESCE 라 보존해버린다.
    # 지워야 open_stock_status 가 상태 행을 연다
    master.clear_delisted(cur, changes.relisted)

    boards = {s.stock_id: s.board for s in incoming}
    opening = changes.listed + changes.moved + changes.relisted
    master.open_stock_status(
        cur,
        [
            StockStatus(stock_id=sid, valid_from=day, board=boards[sid])
            for sid in opening
        ],
    )


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    params = load_config("collect")["stock_master_backfill"]
    # 수집기 CLI 다. 전략이나 피드가 아니므로 현재 시각을 직접 읽어도 된다
    bas_dd = argv[1] if len(argv) > 1 else datetime.now(SEOUL).strftime("%Y%m%d")
    day = date.fromisoformat(bas_dd)

    incoming = collect(bas_dd)
    if not incoming:
        print(f"{bas_dd} 에 종목 데이터가 없습니다.")
        return 1

    with connect() as conn, transaction(conn) as cur:
        # 과거 날짜로 돌리면 그 뒤에 상장한 종목이 폐지로 잡힌다. 시간을 되돌리지 않는다.
        # 종목기본정보는 휴장일에도 응답하므로 아무 날짜나 넘어올 수 있다
        latest = master.latest_status_date(cur)
        if latest is not None and latest > day:
            print(f"이미 {latest} 까지 반영돼 있습니다. {day} 로는 되돌릴 수 없습니다.")
            return 2

        known = master.listed_boards(cur)

        # 빈 응답이나 부분 응답을 '전 종목 폐지' 로 읽으면 stock 이 통째로 망가진다
        if known and len(incoming) < len(known) * (1 - params["max_shrink_ratio"]):
            raise SnapshotShrank(
                f"{day} 스냅샷이 {len(incoming)}건입니다."
                f" 상장중 {len(known)}건 대비 급감했습니다."
            )

        changes = detect(incoming, known, master.delisted_ids(cur))
        apply(cur, day, incoming, changes)
        log_event(
            cur,
            "stock_status",
            "INFO",
            "종목 상태 갱신",
            category="collect",
            detail={
                "bas_dd": bas_dd,
                "listed": changes.listed,
                "delisted": changes.delisted,
                "moved": changes.moved,
                "relisted": changes.relisted,
            },
        )

    for label, ids in (
        ("상장", changes.listed),
        ("폐지", changes.delisted),
        ("이전상장", changes.moved),
    ):
        if ids:
            logger.info("%s %d건: %s", label, len(ids), ids[:10])

    if changes.relisted:
        # 정상이라면 드물다. 잦으면 폐지 감지가 오탐을 내고 있다는 뜻이다
        logger.warning(
            "폐지됐던 종목이 다시 나타나 표시를 지웠습니다 %d건: %s",
            len(changes.relisted),
            changes.relisted[:10],
        )

    print(
        f"{bas_dd} 상태 갱신. 상장 {len(changes.listed)},"
        f" 폐지 {len(changes.delisted)}, 이전상장 {len(changes.moved)}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
