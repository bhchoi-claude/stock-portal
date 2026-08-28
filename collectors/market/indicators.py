# 키움 지수에서 시장분석 지표를 만드는 수집기와 CLI

from __future__ import annotations

import itertools
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from common.broker.kiwoom import KiwoomBroker
from common.config import load_config
from common.db.conn import connect, transaction
from common.db.prices import foreign_net_by_date, listed_stock_ids
from common.notify.telegram import TelegramNotifier
from common.types import IndexClose

from ..base import Collector, CollectResult, IndicatorRecord
from .indicator_runner import run
from .public_indicators import (
    WON_PER_EOK,
    CustomsExportCollector,
    EcosCollector,
    KofiaCollector,
)

logger = logging.getLogger(__name__)

SEOUL = ZoneInfo("Asia/Seoul")


def ma_gap_records(
    closes: list[IndexClose], window: int, since: date
) -> list[IndicatorRecord]:
    """이동평균 이격도(%) 를 만든다.

    이격도 = (종가 - N일 이동평균) / N일 이동평균 x 100.

    앞의 window-1 일은 평균을 낼 수 없어 값이 없다. 0 으로 채우지 않는다.
    `closes` 는 시간 오름차순이어야 한다.
    """
    records = []
    for i in range(window - 1, len(closes)):
        bar = closes[i]
        if bar.trade_date < since:
            continue
        average = sum(c.close for c in closes[i - window + 1 : i + 1]) / window
        gap = (bar.close - average) / average * 100
        records.append(IndicatorRecord("KOSPI_MA200_GAP", bar.trade_date, gap))
    return records


class VkospiCollector(Collector):
    """변동성지수를 그대로 지표로 쓴다."""

    indicator_code = "VKOSPI"
    source_kind = "kiwoom"
    source_identifier = "ka20006"
    interval_sec = 86400

    def __init__(self, broker: KiwoomBroker, index_code: str, end: date) -> None:
        self._broker = broker
        self._index_code = index_code
        self._end = end

    def collect(self, since: datetime) -> CollectResult:
        closes = self._broker.get_index_closes(self._index_code, self._end)
        start = since.date()
        return CollectResult(
            success=True,
            records=[
                IndicatorRecord("VKOSPI", c.trade_date, c.close)
                for c in closes
                if c.trade_date >= start
            ],
        )


class KospiMaGapCollector(Collector):
    """KOSPI 이동평균 이격도. 수집이 아니라 파생 계산이다."""

    indicator_code = "KOSPI_MA200_GAP"
    source_kind = "kiwoom"
    source_identifier = "ka20006"
    interval_sec = 86400

    def __init__(
        self, broker: KiwoomBroker, index_code: str, end: date, window: int
    ) -> None:
        self._broker = broker
        self._index_code = index_code
        self._end = end
        self._window = window

    def collect(self, since: datetime) -> CollectResult:
        closes = self._broker.get_index_closes(self._index_code, self._end)
        if len(closes) < self._window:
            return CollectResult(
                success=False,
                error=f"이동평균에 {self._window}일이 필요한데 {len(closes)}일뿐입니다.",
            )
        return CollectResult(
            success=True,
            records=ma_gap_records(closes, self._window, since.date()),
        )


class ForeignNetCollector(Collector):
    """시장 전체 외국인 순매수. `trading_flow` 를 거래일별로 합친다.

    수집이 아니라 이미 받은 것을 집계하는 파생 지표다.
    """

    indicator_code = "FOREIGN_NET"
    source_kind = "kiwoom"
    source_identifier = "ka10059"
    interval_sec = 86400

    def __init__(self, min_coverage_ratio: float) -> None:
        self._min_coverage_ratio = min_coverage_ratio

    def collect(self, since: datetime) -> CollectResult:
        start = since.date()
        with connect() as conn, transaction(conn) as cur:
            listed = len(listed_stock_ids(cur))
            # 일부만 수집된 날은 뺀다. 합계가 시장 전체를 뜻하지 않게 된다
            min_stocks = int(listed * self._min_coverage_ratio)
            totals = foreign_net_by_date(cur, min_stocks)

        return CollectResult(
            success=True,
            records=[
                # indicator 표가 단위를 억원으로 정의한다
                IndicatorRecord("FOREIGN_NET", day, total / WON_PER_EOK)
                for day, total in totals
                if day >= start
            ],
        )


def daily_returns(closes: list[IndexClose]) -> dict[date, Decimal]:
    """전일 대비 등락률(%). 첫날은 이전 값이 없어 빠진다."""
    returns = {}
    for prev, cur in itertools.pairwise(closes):
        if prev.close:
            returns[cur.trade_date] = (cur.close - prev.close) / prev.close * 100
    return returns


def fill_market_returns(kospi: list[IndexClose], kosdaq: list[IndexClose]) -> int:
    """판정일의 시장 등락률을 채운다. 판정 다음 날 채우는 값이다.

    이것이 있어야 나중에 '위험 판정일의 시장이 실제로 어땠는지' 를 볼 수 있다
    (SCHEMA.md market_regime).
    """
    kospi_returns = daily_returns(kospi)
    kosdaq_returns = daily_returns(kosdaq)

    with connect() as conn, transaction(conn) as cur:
        cur.execute("SELECT trade_date FROM market_regime WHERE kospi_return IS NULL")
        pending = [row[0] for row in cur.fetchall()]
        filled = [
            (kospi_returns[day], kosdaq_returns.get(day), day)
            for day in pending
            if day in kospi_returns
        ]
        if filled:
            cur.executemany(
                "UPDATE market_regime SET kospi_return = %s, kosdaq_return = %s"
                " WHERE trade_date = %s",
                filled,
            )
    return len(filled)


def customs_range(end: date, params: dict) -> tuple[str, str]:
    """관세청 조회 구간(YYYYMM). 월 단위라 개월수로 거슬러 올라간다."""
    months = params["customs_months"]
    year, month = end.year, end.month - months
    while month <= 0:
        year, month = year - 1, month + 12
    return f"{year}{month:02d}", f"{end.year}{end.month:02d}"


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    params = load_config("collect")["indicators"]
    # 수집기 CLI 다. 전략이나 피드가 아니므로 현재 시각을 직접 읽어도 된다
    end = date.fromisoformat(argv[1]) if len(argv) > 1 else datetime.now(SEOUL).date()
    since = datetime.combine(
        end - timedelta(days=params["backfill_days"]), datetime.min.time(), UTC
    )

    broker = KiwoomBroker(is_paper=params["use_paper"])
    collectors = [
        VkospiCollector(broker, params["vkospi_code"], end),
        KospiMaGapCollector(broker, params["kospi_code"], end, params["ma_window"]),
        ForeignNetCollector(params["min_flow_coverage_ratio"]),
        KofiaCollector(
            "DEPOSIT",
            "getSecuritiesMarketTotalCapitalInfo",
            "invrDpsgAmt",
            params["kofia_rows"],
        ),
        KofiaCollector(
            "CREDIT_BALANCE",
            "getGrantingOfCreditBalanceInfo",
            "crdTrFingWhl",
            params["kofia_rows"],
        ),
        EcosCollector(
            "USDKRW",
            params["ecos_fx_stat"],
            params["ecos_fx_item"],
            end,
            params["ecos_days"],
        ),
        CustomsExportCollector("EXPORT_YOY", *customs_range(end, params)),
        CustomsExportCollector(
            "EXPORT_SEMI_YOY",
            *customs_range(end, params),
            hs_code=params["semiconductor_hs"],
        ),
    ]

    try:
        notifier = TelegramNotifier.from_env()
    except RuntimeError:
        logger.exception("알림 설정이 없어 실패해도 알리지 못합니다")
        notifier = None

    outcomes = run(collectors, since, notifier=notifier)
    for outcome in outcomes:
        logger.info(
            "%s %s %d건 %s",
            outcome.name,
            "성공" if outcome.success else "실패",
            outcome.records,
            outcome.error or "",
        )

    filled = fill_market_returns(
        broker.get_index_closes(params["kospi_code"], end),
        broker.get_index_closes(params["kosdaq_code"], end),
    )

    failed = [o.name for o in outcomes if not o.success]
    print(
        f"지표 {sum(o.records for o in outcomes)}건 적재,"
        f" 시장 등락률 {filled}일 채움, 실패 {len(failed)}건."
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
