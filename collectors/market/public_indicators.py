# 금융투자협회·관세청·ECOS 에서 지표를 받는 수집기들

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from ..base import Collector, CollectResult, IndicatorRecord
from .public_api import data_go_kr_json, data_go_kr_xml, ecos_rows

logger = logging.getLogger(__name__)

KOFIA = "1160100/service/GetKofiaStatisticsInfoService"

# 관세청 응답 마지막 행은 기간 합계다. 월이 아니므로 걸러야 한다
CUSTOMS_TOTAL_ROW = "총계"


def to_decimal(value: str) -> Decimal | None:
    """빈 문자열과 잘못된 값을 None 으로 돌린다. 0 으로 만들지 않는다."""
    try:
        return Decimal(value.strip())
    except (InvalidOperation, AttributeError):
        return None


def month_start(year_month: str) -> date | None:
    """관세청의 `2026.06` 을 월초 날짜로 바꾼다. `총계` 행은 None."""
    if CUSTOMS_TOTAL_ROW in year_month:
        return None
    year, _, month = year_month.partition(".")
    return date(int(year), int(month), 1)


class KofiaCollector(Collector):
    """금융투자협회 통계 한 필드를 지표로 만든다."""

    source_kind = "kofia"
    interval_sec = 86400

    def __init__(
        self, indicator_code: str, operation: str, field: str, rows: int
    ) -> None:
        self.indicator_code = indicator_code
        self.source_identifier = operation
        self._operation = operation
        self._field = field
        self._rows = rows

    def collect(self, since: datetime) -> CollectResult:
        items = data_go_kr_json(
            f"{KOFIA}/{self._operation}", numOfRows=self._rows, pageNo=1
        )
        start = since.date()
        records = []
        for item in items:
            value = to_decimal(item.get(self._field, ""))
            day = date.fromisoformat(item["basDt"])
            if value is not None and day >= start:
                records.append(IndicatorRecord(self.indicator_code, day, value))
        return CollectResult(success=True, records=records)


class CustomsExportCollector(Collector):
    """관세청 수출액을 지표로 만든다. 월 단위다.

    `hs_code` 를 주면 품목별 API 를 쓴다. 응답이 10자리 세부코드로 쪼개져
    오므로 같은 달끼리 합산한다 (HS 8542 는 모노리식·디램 등으로 나뉜다).
    """

    source_kind = "customs"
    interval_sec = 86400

    def __init__(
        self,
        indicator_code: str,
        start_ym: str,
        end_ym: str,
        hs_code: str | None = None,
    ) -> None:
        self.indicator_code = indicator_code
        self.source_identifier = "Itemtrade" if hs_code else "Newtrade"
        self._start_ym = start_ym
        self._end_ym = end_ym
        self._hs_code = hs_code

    def collect(self, since: datetime) -> CollectResult:
        if self._hs_code:
            items = data_go_kr_xml(
                "1220000/Itemtrade/getItemtradeList",
                strtYymm=self._start_ym,
                endYymm=self._end_ym,
                hsSgn=self._hs_code,
            )
        else:
            items = data_go_kr_xml(
                "1220000/Newtrade/getNewtradeList",
                strtYymm=self._start_ym,
                endYymm=self._end_ym,
            )

        # 세부코드가 여러 행으로 오므로 달별로 더한다
        totals: dict[date, Decimal] = {}
        for item in items:
            day = month_start(item.get("year", ""))
            amount = to_decimal(item.get("expDlr", ""))
            if day is None or amount is None:
                continue
            totals[day] = totals.get(day, Decimal(0)) + amount

        start = since.date()
        return CollectResult(
            success=True,
            records=[
                IndicatorRecord(self.indicator_code, day, amount)
                for day, amount in sorted(totals.items())
                if day >= start
            ],
        )


class EcosCollector(Collector):
    """ECOS 통계표 한 항목을 지표로 만든다."""

    source_kind = "ecos"
    interval_sec = 86400

    def __init__(
        self,
        indicator_code: str,
        stat_code: str,
        item_code: str,
        end: date,
        days: int,
        cycle: str = "D",
    ) -> None:
        self.indicator_code = indicator_code
        self.source_identifier = stat_code
        self._stat_code = stat_code
        self._item_code = item_code
        self._end = end
        self._days = days
        self._cycle = cycle

    def collect(self, since: datetime) -> CollectResult:
        start = since.date()
        rows = ecos_rows(
            self._stat_code,
            self._cycle,
            start.strftime("%Y%m%d"),
            self._end.strftime("%Y%m%d"),
            self._item_code,
            self._days,
        )
        records = []
        for row in rows:
            value = to_decimal(str(row.get("DATA_VALUE", "")))
            if value is not None:
                records.append(
                    IndicatorRecord(
                        self.indicator_code,
                        date.fromisoformat(row["TIME"]),
                        value,
                    )
                )
        return CollectResult(success=True, records=records)
