# 기준 데이터(휴장일·종목·종목상태·계좌·소스) 테이블 접근 함수

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import psycopg

from ..types import StockState
from .models import Account, Holiday, Source, Stock, StockStatus

# SELECT 와 dataclass 필드 순서를 한 곳에서 맞춘다. 어긋나면 test_schema_drift 가 잡는다.
STOCK_COLUMNS = (
    "stock_id",
    "exchange",
    "code",
    "board",
    "name",
    "sector",
    "listed_shares",
    "is_managed",
    "is_suspended",
    "is_spac",
    "is_preferred",
    "listed_at",
    "delisted_at",
)


def upsert_holidays(cur: psycopg.Cursor, holidays: Sequence[Holiday]) -> int:
    """휴장일을 삽입하거나 이름을 갱신한다."""
    if not holidays:
        return 0
    cur.executemany(
        """
        INSERT INTO exchange_holiday (exchange, holiday_date, name)
        VALUES (%s, %s, %s)
        ON CONFLICT (exchange, holiday_date) DO UPDATE SET
            -- 역산으로는 이름을 알 수 없다. NULL 로 기존 이름을 지우지 않는다
            name = COALESCE(EXCLUDED.name, exchange_holiday.name)
        """,
        [(h.exchange, h.holiday_date, h.name) for h in holidays],
    )
    return len(holidays)


def upsert_stocks(cur: psycopg.Cursor, stocks: Sequence[Stock]) -> int:
    """종목을 삽입하거나 갱신한다. 행은 삭제하지 않는다 (생존편향 방지).

    sector·listed_shares·상장일은 COALESCE 로 덮어쓴다. 출처마다 주는 필드가
    달라서, 그 필드가 없는 출처로 적재할 때 기존 값을 지우면 안 된다.

    `is_managed`·`is_suspended` 는 갱신 시 **손대지 않는다.** 이 둘의 출처는
    KRX 가 아니라 키움이고 `stock_flags` 수집기가 소유한다. 여기서 덮으면
    KRX 로 종목 마스터를 돌릴 때마다 FALSE 로 되돌아간다.
    신규 삽입 시에는 기본값 FALSE 로 들어가고 그 수집기가 채운다.
    """
    if not stocks:
        return 0
    cur.executemany(
        """
        INSERT INTO stock (
            stock_id, exchange, code, board, name, sector, listed_shares,
            is_managed, is_suspended, is_spac, is_preferred,
            listed_at, delisted_at, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (stock_id) DO UPDATE SET
            board         = EXCLUDED.board,
            name          = EXCLUDED.name,
            sector        = COALESCE(EXCLUDED.sector, stock.sector),
            listed_shares = COALESCE(EXCLUDED.listed_shares, stock.listed_shares),
            is_managed    = stock.is_managed,
            is_suspended  = stock.is_suspended,
            is_spac       = EXCLUDED.is_spac,
            is_preferred  = EXCLUDED.is_preferred,
            listed_at     = COALESCE(EXCLUDED.listed_at, stock.listed_at),
            delisted_at   = COALESCE(EXCLUDED.delisted_at, stock.delisted_at),
            updated_at    = NOW()
        """,
        [
            (
                s.stock_id,
                s.exchange,
                s.code,
                s.board,
                s.name,
                s.sector,
                s.listed_shares,
                s.is_managed,
                s.is_suspended,
                s.is_spac,
                s.is_preferred,
                s.listed_at,
                s.delisted_at,
            )
            for s in stocks
        ],
    )
    return len(stocks)


def open_stock_status(cur: psycopg.Cursor, statuses: Sequence[StockStatus]) -> int:
    """열린 상태 행이 없는 상장중인 종목에만 새 행을 연다.

    변경 감지와 이력 종료(valid_to 채우기)는 Phase 2 의 상태 갱신 배치가 맡는다.
    여기서는 적재 시점의 최초 1행만 만든다.

    폐지 종목은 제외한다. 열린 행(valid_to IS NULL)이 생기면 폐지된 종목이
    현재 상태를 가진 것으로 남는다. 과거 날짜로 종목 마스터를 돌리면
    그때 살아 있던 폐지 종목이 그대로 들어온다.
    """
    if not statuses:
        return 0
    cur.executemany(
        """
        INSERT INTO stock_status (stock_id, valid_from, valid_to, board,
                                  is_managed, is_suspended)
        SELECT %s, %s, NULL, %s, %s, %s
        WHERE NOT EXISTS (
            SELECT 1 FROM stock_status WHERE stock_id = %s AND valid_to IS NULL
        )
        AND EXISTS (
            SELECT 1 FROM stock WHERE stock_id = %s AND delisted_at IS NULL
        )
        ON CONFLICT (stock_id, valid_from) DO NOTHING
        """,
        [
            (
                s.stock_id,
                s.valid_from,
                s.board,
                s.is_managed,
                s.is_suspended,
                s.stock_id,
                s.stock_id,
            )
            for s in statuses
        ],
    )
    return len(statuses)


def upsert_accounts(cur: psycopg.Cursor, accounts: Sequence[Account]) -> int:
    """계좌를 삽입하거나 갱신한다. 계좌번호는 DB 에 넣지 않는다 (SCHEMA.md 1장)."""
    if not accounts:
        return 0
    cur.executemany(
        """
        INSERT INTO account (account_id, broker, strategy, is_paper, currency, is_active)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (account_id) DO UPDATE SET
            broker    = EXCLUDED.broker,
            strategy  = EXCLUDED.strategy,
            is_paper  = EXCLUDED.is_paper,
            currency  = EXCLUDED.currency,
            is_active = EXCLUDED.is_active
        """,
        [
            (a.account_id, a.broker, a.strategy, a.is_paper, a.currency, a.is_active)
            for a in accounts
        ],
    )
    return len(accounts)


def upsert_sources(cur: psycopg.Cursor, sources: Sequence[Source]) -> int:
    """수집 소스를 삽입하거나 갱신한다. source_id 는 DB 가 매긴다."""
    if not sources:
        return 0
    cur.executemany(
        """
        INSERT INTO source (kind, identifier, name, weight, is_active)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (kind, identifier) DO UPDATE SET
            name      = EXCLUDED.name,
            weight    = EXCLUDED.weight,
            is_active = EXCLUDED.is_active
        """,
        [(s.kind, s.identifier, s.name, s.weight, s.is_active) for s in sources],
    )
    return len(sources)


def listed_boards(cur: psycopg.Cursor) -> dict[str, str]:
    """상장중인 종목의 시장 구분. 폐지·이전상장 감지의 '이전 적재' 쪽이다."""
    cur.execute("SELECT stock_id, board FROM stock WHERE delisted_at IS NULL")
    return dict(cur.fetchall())


def mark_delisted(cur: psycopg.Cursor, stock_ids: Sequence[str], day: date) -> int:
    """폐지일을 기록한다. 행을 삭제하지 않는다 (생존편향 방지).

    이미 폐지로 표시된 종목은 건드리지 않는다. 감지일은 실제 폐지일보다
    늦을 수 있어서, 정확한 값은 refine_delisted_at 이 일봉에서 다시 맞춘다.
    """
    if not stock_ids:
        return 0
    cur.execute(
        "UPDATE stock SET delisted_at = %s, updated_at = NOW()"
        " WHERE stock_id = ANY(%s) AND delisted_at IS NULL",
        (day, list(stock_ids)),
    )
    return cur.rowcount


def latest_status_date(cur: psycopg.Cursor) -> date | None:
    """상태 이력에 기록된 가장 늦은 날. 과거 날짜로 상태를 되돌리는 것을 막는다."""
    cur.execute("SELECT MAX(valid_from) FROM stock_status")
    return cur.fetchone()[0]


def delisted_ids(cur: psycopg.Cursor) -> set[str]:
    """폐지로 표시된 종목. 이들이 다시 나타나면 감지가 틀렸거나 재상장이다."""
    cur.execute("SELECT stock_id FROM stock WHERE delisted_at IS NOT NULL")
    return {row[0] for row in cur.fetchall()}


def clear_delisted(cur: psycopg.Cursor, stock_ids: Sequence[str]) -> int:
    """폐지 표시를 지운다. 폐지됐던 종목이 다시 마스터에 나타났을 때 쓴다.

    upsert_stocks 는 delisted_at 을 COALESCE 로 보존하므로 여기서 따로 지운다.
    한 번의 잘못된 스냅샷으로 생긴 오탐이 스스로 풀리게 하는 경로다.
    """
    if not stock_ids:
        return 0
    cur.execute(
        "UPDATE stock SET delisted_at = NULL, updated_at = NOW()"
        " WHERE stock_id = ANY(%s)",
        (list(stock_ids),),
    )
    return cur.rowcount


def close_stock_status(cur: psycopg.Cursor, stock_ids: Sequence[str], day: date) -> int:
    """열린 상태 행을 끝낸다. valid_to 는 배타적이라 day 부터는 유효하지 않다."""
    if not stock_ids:
        return 0
    cur.execute(
        "UPDATE stock_status SET valid_to = %s"
        " WHERE stock_id = ANY(%s) AND valid_to IS NULL",
        (day, list(stock_ids)),
    )
    return cur.rowcount


def open_statuses(cur: psycopg.Cursor) -> dict[str, tuple[date, bool, bool]]:
    """열려 있는 상태 행. 플래그 변경 감지의 '이전 적재' 쪽이다."""
    cur.execute(
        "SELECT stock_id, valid_from, is_managed, is_suspended"
        " FROM stock_status WHERE valid_to IS NULL"
    )
    return {row[0]: (row[1], row[2], row[3]) for row in cur.fetchall()}


def set_status_flags(cur: psycopg.Cursor, states: Sequence[StockState]) -> int:
    """열린 상태 행의 플래그를 제자리에서 고친다.

    그 행이 오늘 열렸을 때만 쓴다. 오늘 열린 행을 오늘 끊고 다시 열면
    길이 0 인 구간이 생기고, `valid_from` 이 같아 새 행이 들어가지도 못한다.
    """
    if not states:
        return 0
    cur.executemany(
        "UPDATE stock_status SET is_managed = %s, is_suspended = %s"
        " WHERE stock_id = %s AND valid_to IS NULL",
        [(s.is_managed, s.is_suspended, s.stock_id) for s in states],
    )
    return len(states)


def set_stock_flags(cur: psycopg.Cursor, states: Sequence[StockState]) -> int:
    """stock 의 현재값 캐시를 맞춘다. 이력은 stock_status 가 정본이다."""
    if not states:
        return 0
    cur.executemany(
        "UPDATE stock SET is_managed = %s, is_suspended = %s, updated_at = NOW()"
        " WHERE stock_id = %s",
        [(s.is_managed, s.is_suspended, s.stock_id) for s in states],
    )
    return len(states)


def refine_delisted_at(cur: psycopg.Cursor) -> int:
    """폐지일을 일봉의 마지막 거래일 다음 날로 맞춘다. 갱신된 행 수를 돌려준다.

    이미 폐지로 표시된 종목만 손댄다. 새로 폐지를 표시하지 않는다.
    상장중인 종목은 마지막 거래일이 그냥 마지막으로 적재한 날이라 의미가 없다.
    """
    cur.execute(
        """
        UPDATE stock s
        SET delisted_at = p.last_trade + 1, updated_at = NOW()
        FROM (
            SELECT stock_id, MAX(trade_date) AS last_trade
            FROM price_daily GROUP BY stock_id
        ) p
        WHERE s.stock_id = p.stock_id
          AND s.delisted_at IS NOT NULL
          AND s.delisted_at <> p.last_trade + 1
          -- 마지막 날까지 거래된 종목은 건드리지 않는다. 폐지가 아니라
          -- 마스터에서만 빠진 것일 수 있고, 그 경우 폐지일이 틀린 값이 된다
          AND p.last_trade < (SELECT MAX(trade_date) FROM price_daily)
        """
    )
    return cur.rowcount


def still_trading(cur: psycopg.Cursor) -> list[str]:
    """폐지로 표시됐는데 마지막 거래일이 적재 마지막 날인 종목.

    폐지가 아니라 종목 마스터에서만 사라진 것일 수 있다. 폐지일을 매기면
    틀린 값이 들어가므로 호출부가 확인할 수 있게 목록으로 돌려준다.
    """
    cur.execute(
        """
        SELECT s.stock_id FROM stock s JOIN price_daily p USING (stock_id)
        WHERE s.delisted_at IS NOT NULL
        GROUP BY s.stock_id
        HAVING MAX(p.trade_date) = (SELECT MAX(trade_date) FROM price_daily)
        """
    )
    return [row[0] for row in cur.fetchall()]


def get_stock(cur: psycopg.Cursor, stock_id: str) -> Stock | None:
    """종목 한 건을 읽는다. 없으면 None."""
    cur.execute(
        f"SELECT {', '.join(STOCK_COLUMNS)} FROM stock WHERE stock_id = %s",
        (stock_id,),
    )
    row = cur.fetchone()
    return Stock(*row) if row else None


def count_stocks(cur: psycopg.Cursor, *, listed_only: bool = True) -> int:
    """종목 수를 센다. 적재 결과 확인용."""
    if listed_only:
        cur.execute("SELECT COUNT(*) FROM stock WHERE delisted_at IS NULL")
    else:
        cur.execute("SELECT COUNT(*) FROM stock")
    return cur.fetchone()[0]
