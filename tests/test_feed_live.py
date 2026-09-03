# LiveFeed. 백테스트와 같은 값을 주는지가 이 파일의 핵심이다

from datetime import UTC, date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from common.broker.mock import MockBroker
from common.feed.backtest import BacktestFeed
from common.feed.live import LiveFeed
from common.types import Quote

SEOUL = ZoneInfo("Asia/Seoul")

CLOSE = time(15, 30)
UNIVERSE_SIZE = 200
LIQUIDITY_DAYS = 20

STOCK = "KRX:005930"


class _FakeConn:
    """커서일 조회만 답하는 테스트용. LiveFeed 는 autocommit 커넥션만 받는다."""

    autocommit = True

    def __init__(self, latest=None) -> None:
        self.latest = latest
        self.queries = 0

    def cursor(self):
        return _FakeCursor(self)


class _FakeCursor:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.conn.queries += 1

    def fetchone(self):
        # traded_range 는 (MIN, MAX) 를 받는다
        return (self.conn.latest, self.conn.latest)


def _feed(conn, broker=None, moment=None) -> LiveFeed:
    feed = LiveFeed(
        conn,
        broker or MockBroker(),
        close_time=CLOSE,
        universe_size=UNIVERSE_SIZE,
        liquidity_days=LIQUIDITY_DAYS,
    )
    if moment is not None:
        # 시각을 고정한다. now() 를 통해서만 커서일이 정해지는지 함께 본다
        feed.now = lambda: moment
    return feed


# ---- 시각 ----


def test_now_는_utc_다():
    assert _feed(_FakeConn()).now().tzinfo is UTC


def test_커서일은_오늘이_아니라_최신_거래일이다():
    """**KRX 는 D일 데이터를 D+1 에 공개한다.**

    19:00 수집기가 채우는 것은 어제 것이라, 오늘을 커서로 쓰면
    `universe_at` 이 늘 빈 목록을 준다. 2026-09-03 19:00 수집 뒤에도
    최신 일봉은 09-02 였다.
    """
    moment = datetime(2026, 9, 3, 10, 0, tzinfo=UTC)  # 09-03 19:00 KST
    feed = _feed(_FakeConn(latest=date(2026, 9, 2)), moment=moment)

    assert feed.trade_date() == date(2026, 9, 2)


def test_일봉이_없으면_오늘_한국_날짜를_준다():
    """유니버스가 비고 엔진이 판단을 건너뛴다. `BacktestFeed` 와 같은 동작이다.

    거래일은 시장 현지 기준이다 (CLAUDE.md 5). UTC 날짜로 읽으면 오전
    09:00 이전 한국 시각이 전날로 밀린다.
    """
    # 2026-09-01 08:30 KST = 2026-08-31 23:30 UTC
    moment = datetime(2026, 8, 31, 23, 30, tzinfo=UTC)
    feed = _feed(_FakeConn(latest=None), moment=moment)

    assert feed.trade_date().isoformat() == "2026-09-01"


def test_커서일을_종목마다_다시_읽지_않는다():
    """한 번 스캔에 200종목을 돈다. 종목마다 부르면 220만 행을 200번 훑는다."""
    conn = _FakeConn(latest=date(2026, 9, 2))
    feed = _feed(conn, moment=datetime(2026, 9, 3, 10, 0, tzinfo=UTC))

    for _ in range(50):
        feed.trade_date()

    assert conn.queries == 1


def test_시간이_지나면_커서일을_다시_읽는다():
    """09:00 제출 때 잡은 값이 19:00 판단 때는 낡는다.

    그 사이 19:00 수집기가 새 거래일을 넣기 때문이다.
    """
    conn = _FakeConn(latest=date(2026, 9, 2))
    feed = _feed(conn, moment=datetime(2026, 9, 3, 0, 0, tzinfo=UTC))
    assert feed.trade_date() == date(2026, 9, 2)

    conn.latest = date(2026, 9, 3)
    feed.now = lambda: datetime(2026, 9, 3, 10, 0, tzinfo=UTC)

    assert feed.trade_date() == date(2026, 9, 3)
    assert conn.queries == 2


# ---- 봉 ----


def test_일봉만_받는다():
    """분봉은 Phase 10 이다. BacktestFeed 와 같은 제한을 둔다."""
    with pytest.raises(ValueError):
        _feed(_FakeConn()).get_candles(STOCK, "5m", 10)


# ---- 시세 ----


def test_시세는_브로커에서_온다():
    """DB 에는 현재가가 없다. 여기만 브로커를 본다 (INTERFACES.md 3.2)."""
    quote = Quote(
        stock_id=STOCK,
        ts=datetime(2026, 8, 31, 6, 30, tzinfo=UTC),
        price=Decimal(260000),
        bid=None,
        ask=None,
        volume=100,
    )

    got = _feed(_FakeConn(), broker=MockBroker(quotes={STOCK: quote})).get_quote(STOCK)

    assert got.price == Decimal(260000)


# ---- 백테스트와의 등가성 (DB 통합) ----


def _latest_day(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(trade_date) FROM price_daily")
        return cur.fetchone()[0]


def test_두_피드가_같은_봉을_준다(read_conn):
    """같은 거래일이면 값도 시각도 같아야 한다.

    다르면 같은 전략이 백테스트와 실전에서 다른 것을 본다
    (`PROJECT.md` 8.3).
    """
    day = _latest_day(read_conn)
    moment = datetime.combine(day, CLOSE, tzinfo=SEOUL).astimezone(UTC)

    live = _feed(read_conn, moment=moment)
    with read_conn.cursor() as cur:
        back = BacktestFeed(
            cur,
            day,
            close_time=CLOSE,
            universe_size=UNIVERSE_SIZE,
            liquidity_days=LIQUIDITY_DAYS,
        )
        expected = back.get_candles(STOCK, "1d", 30)

    assert live.get_candles(STOCK, "1d", 30) == expected
    assert expected  # 빈 목록끼리 같아서 통과하는 것을 막는다


def test_두_피드가_같은_유니버스를_준다(read_conn):
    """Phase 7 에서 유니버스 기준 하나가 총수익률을 -50.88% 와 -34.38% 로
    갈랐다. 여기서 갈리면 백테스트 결과가 무의미해진다."""
    day = _latest_day(read_conn)
    moment = datetime.combine(day, CLOSE, tzinfo=SEOUL).astimezone(UTC)

    live = _feed(read_conn, moment=moment)
    with read_conn.cursor() as cur:
        back = BacktestFeed(
            cur,
            day,
            close_time=CLOSE,
            universe_size=UNIVERSE_SIZE,
            liquidity_days=LIQUIDITY_DAYS,
        )
        expected = back.get_universe()

    assert live.get_universe() == expected
    assert expected


def test_일봉이_없는_날은_유니버스가_빈다(read_conn):
    """`universe_at` 이 그날 거래된 종목을 요구한다. 19:00 적재 전에는 빈다.

    예외를 던지지 않는다 — `BacktestFeed` 와 달라지면 그것이 갈라짐이다.
    일봉이 쌓였는지는 엔진이 `price_daily` 를 직접 보고 판단한다 (4단계).
    """
    day = _latest_day(read_conn)
    # 마지막 거래일보다 뒤. 아직 아무것도 안 쌓인 날이다
    moment = datetime.combine(day, CLOSE, tzinfo=SEOUL).astimezone(UTC)
    ahead = moment.replace(year=moment.year + 1)

    assert _feed(read_conn, moment=ahead).get_universe() == []


def test_autocommit_이_아니면_거부한다():
    """상주 프로세스가 트랜잭션을 오래 열어두면 안 된다.

    켜주지 않고 요구한다. 켜면 남의 커넥션 상태를 바꾸는 데다, 이미
    트랜잭션이 열려 있으면 생성자가 터진다.
    """

    class _Plain:
        autocommit = False

    with pytest.raises(RuntimeError):
        _feed(_Plain())
