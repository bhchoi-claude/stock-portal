# 키움 ka10099 로 관리종목·거래정지 플래그를 갱신하는 CLI

from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

from common.broker.kiwoom import KiwoomBroker
from common.config import load_config
from common.db import master
from common.db.conn import connect, transaction
from common.db.events import log_event
from common.db.models import StockStatus
from common.types import StockState

logger = logging.getLogger(__name__)

SEOUL = ZoneInfo("Asia/Seoul")


class SnapshotShrank(RuntimeError):
    """응답이 상장 종목 수에 못 미친다. 전 종목 해제로 읽으면 이력이 망가진다."""


def detect(
    states: list[StockState], known: dict[str, tuple[date, bool, bool]]
) -> list[StockState]:
    """플래그가 달라진 종목만 고른다.

    `known` 은 열린 상태 행이다. 폐지 종목에는 열린 행이 없어 저절로 빠진다.
    응답에만 있고 우리 DB 에 없는 종목(ETF 등)도 여기서 걸러진다.
    """
    changed = []
    for state in states:
        row = known.get(state.stock_id)
        if row is None:
            continue
        _, is_managed, is_suspended = row
        if (is_managed, is_suspended) != (state.is_managed, state.is_suspended):
            changed.append(state)
    return changed


def apply(cur, day: date, changed: list[StockState]) -> tuple[int, int]:
    """바뀐 플래그를 stock(현재값)과 stock_status(이력)에 함께 쓴다.

    오늘 열린 행은 제자리에서 고치고, 어제 이전에 열린 행은 끊고 새로 연다.
    돌려주는 값은 (제자리 수정, 새 구간) 건수다.
    """
    known = master.open_statuses(cur)
    today_opened = [s for s in changed if known[s.stock_id][0] == day]
    earlier = [s for s in changed if known[s.stock_id][0] != day]

    master.set_status_flags(cur, today_opened)

    # 끊어야 open_stock_status 가 연다. 열린 행이 있으면 넣지 않는다
    master.close_stock_status(cur, [s.stock_id for s in earlier], day)
    boards = master.listed_boards(cur)
    master.open_stock_status(
        cur,
        [
            StockStatus(
                stock_id=s.stock_id,
                valid_from=day,
                board=boards[s.stock_id],
                is_managed=s.is_managed,
                is_suspended=s.is_suspended,
            )
            for s in earlier
        ],
    )

    master.set_stock_flags(cur, changed)
    return len(today_opened), len(earlier)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    params = load_config("collect")["stock_flags"]
    # 수집기 CLI 다. 전략이나 피드가 아니므로 현재 시각을 직접 읽어도 된다
    day = datetime.now(SEOUL).date()

    broker = KiwoomBroker(is_paper=params["use_paper"])
    states = broker.get_stock_states()
    logger.info("키움 응답 %d건", len(states))

    with connect() as conn, transaction(conn) as cur:
        known = master.open_statuses(cur)

        # 부분 응답을 '전 종목 관리종목 해제' 로 읽으면 이력이 통째로 틀어진다
        matched = sum(1 for s in states if s.stock_id in known)
        if known and matched < len(known) * params["min_coverage_ratio"]:
            raise SnapshotShrank(
                f"응답이 상장중 {len(known)}건 중 {matched}건만 덮습니다."
            )

        changed = detect(states, known)
        updated, opened = apply(cur, day, changed)

        managed = sum(s.is_managed for s in states if s.stock_id in known)
        suspended = sum(s.is_suspended for s in states if s.stock_id in known)
        log_event(
            cur,
            "stock_flags",
            "INFO",
            "종목 플래그 갱신",
            category="collect",
            detail={
                "day": str(day),
                "matched": matched,
                "changed": len(changed),
                "managed": managed,
                "suspended": suspended,
            },
        )

    for state in changed[:20]:
        logger.info(
            "%s 관리종목=%s 거래정지=%s",
            state.stock_id,
            state.is_managed,
            state.is_suspended,
        )

    print(
        f"{day} 플래그 갱신. 대조 {matched}건, 변경 {len(changed)}건"
        f" (제자리 {updated}, 새 구간 {opened})."
        f" 현재 관리종목 {managed}, 거래정지 {suspended}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
