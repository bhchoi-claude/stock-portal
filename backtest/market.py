# 체결측 데이터 조회. 피드가 감추는 값을 여기서만 본다

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol

import psycopg

from common.db.master import delisted_dates
from common.db.prices import board_at, open_on, raw_close, trading_days


class Market(Protocol):
    """**`DataFeed` 와 일부러 나눈 경계다.**

    피드는 커서 이후를 보여주지 않는다. 그런데 체결은 다음 거래일 시가에
    일어나므로 그 값을 누군가는 봐야 한다. 전략이 아니라 루프가 본다는 것을
    타입으로 못박는다. 전략에 이 객체를 넘기지 않는다.
    """

    def trading_days(self, start: date, end: date) -> list[date]: ...

    def open_on(self, stock_id: str, day: date) -> Decimal | None: ...

    def last_close(self, stock_id: str, day: date) -> tuple[date, Decimal] | None: ...

    def board_at(self, stock_id: str, day: date) -> str | None: ...

    def delisted_at(self, stock_id: str) -> date | None: ...


class DbMarket:
    """PostgreSQL 구현. 폐지일만 미리 받아 둔다. 나머지는 그때그때 읽는다."""

    def __init__(self, cur: psycopg.Cursor) -> None:
        self.cur = cur
        self._delisted = delisted_dates(cur)

    def trading_days(self, start: date, end: date) -> list[date]:
        return trading_days(self.cur, start, end)

    def open_on(self, stock_id: str, day: date) -> Decimal | None:
        return open_on(self.cur, stock_id, day)

    def last_close(self, stock_id: str, day: date) -> tuple[date, Decimal] | None:
        """기준일까지의 마지막 종가와 그 거래일. **원주가다.**

        평가와 정리매매 청산에 쓴다. 조정가를 쓰면 보유 수량과 단위가 어긋난다.
        """
        row = raw_close(self.cur, stock_id, day)
        return (row[0], row[1]) if row else None

    def board_at(self, stock_id: str, day: date) -> str | None:
        return board_at(self.cur, stock_id, day)

    def delisted_at(self, stock_id: str) -> date | None:
        return self._delisted.get(stock_id)
