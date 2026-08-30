# 과거 데이터를 커서 시각까지만 보여주는 피드 (INTERFACES.md 3.2)

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg

from ..db.prices import candles_until, raw_close, universe_at
from ..db.regime import regime_history
from ..types import Candle, Quote, Regime, Signal
from .base import DataFeed

SEOUL = ZoneInfo("Asia/Seoul")

DAILY = "1d"


class BacktestFeed(DataFeed):
    """커서 시각이 `now()` 다. 그 뒤의 데이터는 존재하지 않는 것처럼 군다.

    커서는 거래일 단위로 움직이고, 시각은 **그날 장 마감**이다.
    종가가 확정된 시점이라는 뜻이다. 신호는 여기서 계산되고 체결은 다음
    거래일 시가에 일어난다 (2026-08-30 승인).
    """

    def __init__(
        self,
        cur: psycopg.Cursor,
        day: date,
        *,
        close_time: time,
        universe_size: int,
    ) -> None:
        self.cur = cur
        self.day = day
        self.close_time = close_time
        self.universe_size = universe_size

    def set_date(self, day: date) -> None:
        """커서를 옮긴다. 뒤로 돌리는 것도 막지 않는다. 재현에 쓴다."""
        self.day = day

    def now(self) -> datetime:
        """커서일의 장 마감 시각(UTC)."""
        return self._at(self.day)

    def get_candles(self, stock_id: str, interval: str, count: int) -> list[Candle]:
        if interval != DAILY:
            # 분봉 백테스트는 Phase 10 이다. 지금은 나흘치뿐이라 돌릴 수 없다
            raise ValueError(f"일봉만 지원합니다: {interval}")

        return [
            Candle(
                stock_id=stock_id,
                # 일봉의 시각은 그날 장 마감이다. now() 와 같은 기준으로 둔다
                ts=self._at(row[0]),
                open=row[1],
                high=row[2],
                low=row[3],
                close=row[4],
                volume=row[5],
            )
            for row in candles_until(self.cur, stock_id, self.day, count)
        ]

    def get_quote(self, stock_id: str) -> Quote:
        row = raw_close(self.cur, stock_id, self.day)
        if row is None:
            raise LookupError(f"{stock_id} 의 {self.day} 이전 시세가 없습니다.")

        traded_on, close, volume = row
        return Quote(
            stock_id=stock_id,
            ts=self._at(traded_on),
            price=close,
            # 과거 호가는 남아 있지 않다. 없는 것을 지어내지 않는다
            bid=None,
            ask=None,
            volume=volume,
        )

    def get_universe(self) -> list[str]:
        return universe_at(self.cur, self.day, self.universe_size)

    def get_regime(self) -> Regime:
        """커서일 이전의 마지막 판정. 판정이 없으면 중립으로 본다.

        국면 이력은 2026-08 부터만 있다. 그 이전 구간을 돌리면 전부 중립이다.
        없는 판정을 지어내지 않는다.
        """
        rows = regime_history(self.cur, self.day - timedelta(days=30), self.day)
        return Regime(rows[0].regime) if rows else Regime.NEUTRAL

    def get_signals(self, strategy: str, since: datetime) -> list[Signal]:
        """아직 비어 있다. `signal` 적재는 Phase 8 에서 엔진이 한다."""
        return []

    def _at(self, day: date) -> datetime:
        """거래일을 그날 장 마감 시각(UTC)으로. 저장 규칙이 UTC 다 (CLAUDE.md 5)."""
        return datetime.combine(day, self.close_time, tzinfo=SEOUL).astimezone(UTC)
