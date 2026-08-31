# 실전 피드. 백테스트와 같은 DB 함수를 부른다 (INTERFACES.md 3.2)

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg

from ..broker.base import Broker
from ..db.prices import candles_until, universe_at
from ..db.regime import regime_history
from ..types import Candle, Quote, Regime, Signal
from .base import DataFeed

SEOUL = ZoneInfo("Asia/Seoul")

DAILY = "1d"


class LiveFeed(DataFeed):
    """실제 시각으로 도는 피드. **봉과 유니버스는 백테스트와 같은 곳에서 온다.**

    `BacktestFeed` 와 같은 `candles_until` · `universe_at` 을 부른다.
    브로커에서 직접 받으면 데이터 경로가 갈려 백테스트와 실전이 다른 것을
    보게 된다 (`PROJECT.md` 8.3). 시세만 브로커에서 받는다
    (`INTERFACES.md` 3.2).

    **커서일은 `now()` 의 한국 날짜다.** 그날 일봉이 아직 안 쌓였으면
    `get_candles` 는 전날까지를 주고 `get_universe` 는 **빈 목록**을 준다
    (`universe_at` 이 그날 거래된 종목을 요구한다). `BacktestFeed` 와 같은
    동작이다 — 여기서 예외를 던지면 그것이 곧 갈라짐이다. 일봉이 쌓였는지는
    엔진이 `price_daily` 를 직접 보고 판단한다 (4단계).

    커넥션은 **읽기 전용으로만 쓴다.** 주문을 기록하는 커넥션과 공유하지
    않는다. 상주 프로세스가 트랜잭션을 오래 열어두지 않도록 autocommit 으로
    둔다.
    """

    def __init__(
        self,
        conn: psycopg.Connection,
        broker: Broker,
        *,
        close_time: time,
        universe_size: int,
        liquidity_days: int,
    ) -> None:
        self.conn = conn
        self.conn.autocommit = True
        self.broker = broker
        self.close_time = close_time
        self.universe_size = universe_size
        self.liquidity_days = liquidity_days

    def now(self) -> datetime:
        """실제 시각(UTC).

        전략이 아니라 피드의 시계라 여기서만 실제 시각을 읽는다. 전략과
        나머지 피드 코드는 이 값을 통해서만 시각을 안다 (INTERFACES.md 3.1).
        """
        return datetime.now(UTC)

    def get_candles(self, stock_id: str, interval: str, count: int) -> list[Candle]:
        """`BacktestFeed.get_candles` 와 같은 행에서 같은 모양을 만든다.

        시각까지 같게 둔다. 다르면 같은 거래일의 봉이 두 곳에서 다른 값이
        된다. 등가성은 테스트로 고정한다.
        """
        if interval != DAILY:
            # 분봉은 Phase 10 이다. BacktestFeed 와 같은 제한을 둔다
            raise ValueError(f"일봉만 지원합니다: {interval}")

        with self.conn.cursor() as cur:
            rows = candles_until(cur, stock_id, self.trade_date(), count)

        return [
            Candle(
                stock_id=stock_id,
                ts=self._at(row[0]),
                open=row[1],
                high=row[2],
                low=row[3],
                close=row[4],
                volume=row[5],
            )
            for row in rows
        ]

    def get_quote(self, stock_id: str) -> Quote:
        """**여기만 브로커를 본다.** 현재가는 DB 에 없다.

        장 마감 뒤에는 브로커의 현재가가 그날 종가라 `BacktestFeed` 가 주는
        값과 같아진다. 엔진이 19:00 에 판단하므로 그 시점에는 일치한다.
        """
        return self.broker.get_quote(stock_id)

    def get_universe(self) -> list[str]:
        """백테스트와 **같은 함수**로 뽑는다. 기준이 갈리면 안 된다.

        Phase 7 에서 유니버스 기준 하나가 총수익률을 -50.88% 와 -34.38% 로
        갈랐다. 여기서 다른 기준을 쓰면 백테스트 결과가 무의미해진다.
        """
        with self.conn.cursor() as cur:
            return universe_at(
                cur, self.trade_date(), self.universe_size, self.liquidity_days
            )

    def get_regime(self) -> Regime:
        """오늘 이전의 마지막 판정. 없으면 중립으로 본다 (`BacktestFeed` 와 같다)."""
        today = self.trade_date()
        with self.conn.cursor() as cur:
            rows = regime_history(cur, today - timedelta(days=30), today)
        return Regime(rows[0].regime) if rows else Regime.NEUTRAL

    def get_signals(self, strategy: str, since: datetime) -> list[Signal]:
        """아직 부르는 곳이 없다. `BacktestFeed` 와 같이 비어 있다.

        `signal` 적재는 엔진이 하고(4단계), 읽는 쪽이 생기면 그때 만든다.
        """
        return []

    def trade_date(self) -> date:
        """DB 조회의 기준일. `now()` 의 **한국 날짜**다.

        거래일은 시장 현지 기준으로 저장한다 (CLAUDE.md 5). UTC 날짜로
        읽으면 09:00 이전 한국 시각이 전날로 밀린다.
        """
        return self.now().astimezone(SEOUL).date()

    def _at(self, day: date) -> datetime:
        """거래일을 그날 장 마감 시각(UTC)으로. `BacktestFeed._at` 과 같다."""
        return datetime.combine(day, self.close_time, tzinfo=SEOUL).astimezone(UTC)
