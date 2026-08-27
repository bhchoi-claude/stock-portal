# KRX 오픈API 로 전 상장종목을 stock 테이블에 적재하는 CLI

from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from common.db import master
from common.db.conn import connect, transaction
from common.db.events import log_event
from common.db.models import Stock, StockStatus, make_stock_id

from .krx import fetch

logger = logging.getLogger(__name__)

# 시장별로 API 가 나뉜다. board 는 응답의 MKT_TP_NM 과 일치하는 것을 확인했다
STOCK_APIS = {
    "KOSPI": "stk_isu_base_info",
    "KOSDAQ": "ksq_isu_base_info",
    "KONEX": "knx_isu_base_info",
}

# 주권 계열만 담는다. 부동산투자회사(리츠)·투자회사·사회간접자본투융자회사는 제외한다.
# stock 에 증권구분 컬럼이 없어 섞어 넣으면 나중에 구분할 근거가 남지 않는다
EQUITY_TYPES = frozenset({"주권", "외국주권", "주식예탁증권"})

SEOUL = ZoneInfo("Asia/Seoul")


def to_stock(row: dict[str, Any]) -> Stock:
    """KRX 응답 한 행을 Stock 으로 바꾼다."""
    code = row["ISU_SRT_CD"]
    # ISU_NM 은 '삼성전자보통주' 처럼 정식 명칭이라 화면에 쓸 이름이 아니다
    name = row["ISU_ABBRV"]

    return Stock(
        stock_id=make_stock_id("KRX", code),
        exchange="KRX",
        code=code,
        board=row["MKT_TP_NM"],
        name=name,
        listed_shares=int(row["LIST_SHRS"]) if row["LIST_SHRS"] else None,
        # 구형우선주·신형우선주·종류주권을 모두 참으로 본다. 이 플래그의 쓰임이
        # '보통주가 아닌 것을 걸러내기' 이기 때문이다
        is_preferred=row["KIND_STKCERT_TP_NM"] != "보통주",
        # KRX 에 스팩 구분값이 없다. 종목명으로 판정하는 휴리스틱이다
        is_spac="스팩" in name,
        listed_at=parse_date(row["LIST_DD"]),
    )


def parse_date(value: str) -> date | None:
    """LIST_DD 는 20150821 형식이다.

    거래일은 시장 현지 기준 DATE 다. datetime 을 거치면 UTC 변환 유혹이 생긴다.
    """
    return date.fromisoformat(value) if value else None


def collect(bas_dd: str) -> list[Stock]:
    """세 시장을 모두 조회해 주권 계열만 모은다."""
    stocks: list[Stock] = []
    for board, api_id in STOCK_APIS.items():
        rows = fetch("sto", api_id, bas_dd)
        kept = [to_stock(r) for r in rows if r["SECUGRP_NM"] in EQUITY_TYPES]
        logger.info("%s %d건 중 %d건", board, len(rows), len(kept))
        stocks.extend(kept)
    return stocks


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # 수집기 CLI 다. 전략이나 피드가 아니므로 현재 시각을 직접 읽어도 된다
    bas_dd = argv[1] if len(argv) > 1 else datetime.now(SEOUL).strftime("%Y%m%d")

    stocks = collect(bas_dd)
    if not stocks:
        print(f"{bas_dd} 에 데이터가 없습니다.")
        return 1

    valid_from = date.fromisoformat(bas_dd)
    statuses = [
        StockStatus(stock_id=s.stock_id, valid_from=valid_from, board=s.board)
        for s in stocks
    ]

    with connect() as conn, transaction(conn) as cur:
        master.upsert_stocks(cur, stocks)
        master.open_stock_status(cur, statuses)
        log_event(
            cur,
            "stock_master",
            "INFO",
            "종목 마스터 적재",
            category="collect",
            detail={"bas_dd": bas_dd, "count": len(stocks)},
        )
        total = master.count_stocks(cur)

    print(f"{bas_dd} 기준 {len(stocks)}건 적재. stock 테이블 총 {total}건.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
