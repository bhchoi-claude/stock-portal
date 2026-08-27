# KRX 일별매매정보를 price_daily 에 적재하는 CLI

from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from common.db.conn import connect, transaction
from common.db.events import log_event
from common.db.models import PriceDaily, make_stock_id
from common.db.prices import known_stock_ids, upsert_price_daily

from .krx import fetch

logger = logging.getLogger(__name__)

# 종목기본정보와 마찬가지로 시장별로 API 가 나뉜다
TRADE_APIS = {
    "KOSPI": "stk_bydd_trd",
    "KOSDAQ": "ksq_bydd_trd",
    "KONEX": "knx_bydd_trd",
}

SEOUL = ZoneInfo("Asia/Seoul")


def to_price_daily(row: dict[str, Any]) -> PriceDaily:
    """KRX 응답 한 행을 PriceDaily 로 바꾼다.

    이 API 의 ISU_CD 는 단축코드다. 종목기본정보의 ISU_CD(표준코드)와 다르다.
    """
    close = Decimal(row["TDD_CLSPRC"])
    volume = int(row["ACC_TRDVOL"])

    # 거래가 없으면 시·고·저가 0 으로 온다. 종가로 채워 도지 캔들로 만든다.
    # 0 을 그대로 넣으면 low <= open, close <= high 가 깨지고 지표가 망가진다.
    # volume 이 0 으로 남으므로 '거래 없는 날' 이라는 사실은 그대로 복원할 수 있다
    if volume == 0:
        open_ = high = low = close
    else:
        open_ = Decimal(row["TDD_OPNPRC"])
        high = Decimal(row["TDD_HGPRC"])
        low = Decimal(row["TDD_LWPRC"])

    return PriceDaily(
        stock_id=make_stock_id("KRX", row["ISU_CD"]),
        trade_date=date.fromisoformat(row["BAS_DD"]),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        value=Decimal(row["ACC_TRDVAL"]),
    )


def collect(bas_dd: str) -> list[PriceDaily]:
    """세 시장의 하루치 일봉을 모은다."""
    prices: list[PriceDaily] = []
    for board, api_id in TRADE_APIS.items():
        rows = fetch("sto", api_id, bas_dd)
        logger.info("%s %d건", board, len(rows))
        prices.extend(to_price_daily(r) for r in rows)
    return prices


def drop_unknown(
    prices: list[PriceDaily], known: set[str]
) -> tuple[list[PriceDaily], list[str]]:
    """stock 에 없는 종목의 시세를 걸러낸다.

    리츠·투자회사는 시세에는 오지만 stock 에 담지 않았다. 그대로 넣으면
    FK 위반으로 트랜잭션이 통째로 깨진다.
    """
    kept = [p for p in prices if p.stock_id in known]
    skipped = [p.stock_id for p in prices if p.stock_id not in known]
    return kept, skipped


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # 수집기 CLI 다. 전략이나 피드가 아니므로 현재 시각을 직접 읽어도 된다
    bas_dd = argv[1] if len(argv) > 1 else datetime.now(SEOUL).strftime("%Y%m%d")

    prices = collect(bas_dd)
    if not prices:
        print(f"{bas_dd} 에 데이터가 없습니다. 휴장일로 보입니다.")
        return 1

    with connect() as conn, transaction(conn) as cur:
        kept, skipped = drop_unknown(prices, known_stock_ids(cur))
        upsert_price_daily(cur, kept)
        log_event(
            cur,
            "price_daily",
            "INFO",
            "일봉 적재",
            category="collect",
            detail={"bas_dd": bas_dd, "count": len(kept), "skipped": len(skipped)},
        )

    if skipped:
        # 예상은 리츠 계열 26건 안팎이다. 크게 늘면 stock 적재가 밀린 것이다
        logger.warning("stock 에 없어 건너뜀 %d건: %s", len(skipped), skipped[:10])

    print(f"{bas_dd} 일봉 {len(kept)}건 적재, {len(skipped)}건 건너뜀.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
