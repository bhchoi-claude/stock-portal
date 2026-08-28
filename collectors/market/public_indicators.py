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

# indicator 표가 예탁금·신용잔고·수급의 단위를 '억원' 으로 정의한다.
# 원 단위로 넣으면 정의와 어긋나고 NUMERIC(20,6) 도 넘친다 (예탁금 100조)
WON_PER_EOK = Decimal(10**8)

# 관세청은 조회 구간을 1년으로 제한한다 (2026-08-29 실측)
CUSTOMS_MAX_MONTHS = 12


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


def split_months(start_ym: str, end_ym: str, size: int) -> list[tuple[str, str]]:
    """`YYYYMM` 구간을 size 개월 이하로 쪼갠다.

    관세청이 1년을 넘는 조회를 거부한다. 경계에서 달이 겹치지 않게 나눈다.
    """
    start = date(int(start_ym[:4]), int(start_ym[4:]), 1)
    end = date(int(end_ym[:4]), int(end_ym[4:]), 1)

    chunks = []
    cursor = start
    while cursor <= end:
        year, month = cursor.year, cursor.month + size - 1
        year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
        stop = min(date(year, month, 1), end)
        chunks.append((f"{cursor:%Y%m}", f"{stop:%Y%m}"))
        year, month = stop.year, stop.month + 1
        cursor = date(year + (month - 1) // 12, (month - 1) % 12 + 1, 1)
    return chunks


class KofiaCollector(Collector):
    """금융투자협회 통계 한 필드를 지표로 만든다."""

    source_kind = "kofia"
    interval_sec = 86400

    def __init__(
        self,
        indicator_code: str,
        operation: str,
        field: str,
        rows: int,
        divisor: Decimal = WON_PER_EOK,
    ) -> None:
        self.indicator_code = indicator_code
        self.source_identifier = operation
        self._operation = operation
        self._field = field
        self._rows = rows
        self._divisor = divisor

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
                records.append(
                    IndicatorRecord(self.indicator_code, day, value / self._divisor)
                )
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

    def _fetch(self, start_ym: str, end_ym: str) -> list[dict[str, str]]:
        if self._hs_code:
            return data_go_kr_xml(
                "1220000/Itemtrade/getItemtradeList",
                strtYymm=start_ym,
                endYymm=end_ym,
                hsSgn=self._hs_code,
            )
        return data_go_kr_xml(
            "1220000/Newtrade/getNewtradeList", strtYymm=start_ym, endYymm=end_ym
        )

    def collect(self, since: datetime) -> CollectResult:
        # 관세청은 한 번에 1년까지만 준다. 구간을 쪼개 여러 번 부른다
        items: list[dict[str, str]] = []
        for chunk_start, chunk_end in split_months(
            self._start_ym, self._end_ym, CUSTOMS_MAX_MONTHS
        ):
            items += self._fetch(chunk_start, chunk_end)

        # 세부코드가 여러 행으로 오므로 달별로 더한다
        totals: dict[date, Decimal] = {}
        for item in items:
            day = month_start(item.get("year", ""))
            amount = to_decimal(item.get("expDlr", ""))
            if day is None or amount is None:
                continue
            totals[day] = totals.get(day, Decimal(0)) + amount

        # indicator 표가 단위를 % 로 정의한다. 코드 이름도 YOY 다.
        # 금액이 아니라 전년 동월 대비 증가율을 넣는다
        return CollectResult(success=True, records=self._to_yoy(totals, since.date()))

    def _to_yoy(
        self, totals: dict[date, Decimal], start: date
    ) -> list[IndicatorRecord]:
        """전년 동월 대비 증가율(%). 12개월 전 값이 없으면 만들지 않는다."""
        records = []
        for day, amount in sorted(totals.items()):
            if day < start:
                continue
            previous = totals.get(day.replace(year=day.year - 1))
            if previous is None or previous == 0:
                logger.info("%s 는 전년 동월 값이 없어 건너뜁니다", day)
                continue
            rate = (amount - previous) / previous * 100
            records.append(IndicatorRecord(self.indicator_code, day, rate))
        return records


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
