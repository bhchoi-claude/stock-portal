# 실제 PostgreSQL 에 붙여 기준 데이터 upsert 와 조회를 확인한다. 모든 행은 롤백된다

from dataclasses import replace
from datetime import date
from decimal import Decimal

import psycopg
import pytest

from common.db import master
from common.db.conn import load_database_url
from common.db.events import log_event
from common.db.models import Account, Holiday, Source, Stock, StockStatus, make_stock_id

TEST_CODE = "999990"
TEST_STOCK_ID = make_stock_id("KRX", TEST_CODE)


@pytest.fixture
def cur():
    """트랜잭션을 열고 테스트가 끝나면 롤백한다. DB 에 흔적을 남기지 않는다."""
    try:
        url = load_database_url()
    except RuntimeError:
        pytest.skip("DATABASE_URL 이 없어 DB 통합 테스트를 건너뜁니다")

    conn = psycopg.connect(url)
    try:
        with conn.cursor() as c:
            yield c
    finally:
        conn.rollback()
        conn.close()


def sample_stock(**overrides) -> Stock:
    base = {
        "stock_id": TEST_STOCK_ID,
        "exchange": "KRX",
        "code": TEST_CODE,
        "board": "KOSPI",
        "name": "테스트종목",
        "sector": "전기전자",
        "listed_shares": 1000,
        "listed_at": date(2020, 1, 2),
    }
    return Stock(**{**base, **overrides})


def test_get_stock_은_컬럼_순서대로_읽는다(cur):
    master.upsert_stocks(cur, [sample_stock()])
    got = master.get_stock(cur, TEST_STOCK_ID)

    assert got == sample_stock()


def test_없는_종목은_none(cur):
    assert master.get_stock(cur, "KRX:000000") is None


def test_재적재는_기존_값을_지우지_않는다(cur):
    master.upsert_stocks(cur, [sample_stock()])
    # sector·listed_shares·listed_at 이 없는 출처로 다시 적재한다
    master.upsert_stocks(
        cur,
        [
            sample_stock(
                name="테스트종목우", sector=None, listed_shares=None, listed_at=None
            )
        ],
    )

    got = master.get_stock(cur, TEST_STOCK_ID)
    assert got.name == "테스트종목우"
    assert got.sector == "전기전자"
    assert got.listed_shares == 1000
    assert got.listed_at == date(2020, 1, 2)


def test_상태는_열린_행이_있으면_새로_열지_않는다(cur):
    master.upsert_stocks(cur, [sample_stock()])
    status = StockStatus(
        stock_id=TEST_STOCK_ID, valid_from=date(2026, 8, 25), board="KOSPI"
    )
    master.open_stock_status(cur, [status])
    master.open_stock_status(cur, [replace(status, valid_from=date(2026, 8, 26))])

    cur.execute(
        "SELECT COUNT(*) FROM stock_status WHERE stock_id = %s AND valid_to IS NULL",
        (TEST_STOCK_ID,),
    )
    assert cur.fetchone()[0] == 1


def test_휴장일_upsert(cur):
    master.upsert_holidays(cur, [Holiday("KRX", date(2026, 1, 1), "신정")])
    master.upsert_holidays(cur, [Holiday("KRX", date(2026, 1, 1), "신정(수정)")])

    cur.execute(
        "SELECT name FROM exchange_holiday WHERE exchange='KRX' AND holiday_date=%s",
        (date(2026, 1, 1),),
    )
    assert cur.fetchone()[0] == "신정(수정)"


def test_계좌_upsert(cur):
    # 실계좌를 건드리지 않는다. 모의 계좌만 쓴다 (CLAUDE.md 실계좌 보호)
    account = Account(
        account_id="test_paper", broker="kiwoom", strategy="swing", is_paper=True
    )
    master.upsert_accounts(cur, [account])
    master.upsert_accounts(cur, [account])

    cur.execute("SELECT is_paper FROM account WHERE account_id='test_paper'")
    assert cur.fetchone()[0] is True


def test_소스_upsert(cur):
    src = Source(
        kind="dart", identifier="test-dart", name="테스트", weight=Decimal("1.5")
    )
    master.upsert_sources(cur, [src])
    master.upsert_sources(cur, [src])

    cur.execute("SELECT COUNT(*), MAX(weight) FROM source WHERE identifier='test-dart'")
    count, weight = cur.fetchone()
    assert (count, weight) == (1, Decimal("1.5"))


def test_빈_목록은_아무것도_하지_않는다(cur):
    assert master.upsert_stocks(cur, []) == 0
    assert master.upsert_holidays(cur, []) == 0
    assert master.open_stock_status(cur, []) == 0


def test_event_log_기록(cur):
    event_id = log_event(
        cur, "pytest", "INFO", "테스트 이벤트", category="system", detail={"k": 1}
    )
    cur.execute("SELECT detail FROM event_log WHERE event_id = %s", (event_id,))
    assert cur.fetchone()[0] == {"k": 1}


def test_폐지일을_마지막_거래일_다음날로_맞춘다(cur):
    stock = sample_stock(delisted_at=date(2024, 1, 1))
    master.upsert_stocks(cur, [stock])
    cur.executemany(
        "INSERT INTO price_daily (stock_id, trade_date, open, high, low, close, volume)"
        " VALUES (%s, %s, 100, 100, 100, 100, 1)",
        [(TEST_STOCK_ID, date(2023, 12, 20)), (TEST_STOCK_ID, date(2023, 12, 21))],
    )

    master.refine_delisted_at(cur)

    assert master.get_stock(cur, TEST_STOCK_ID).delisted_at == date(2023, 12, 22)


def test_상장중인_종목은_폐지일을_매기지_않는다(cur):
    master.upsert_stocks(cur, [sample_stock(delisted_at=None)])
    cur.execute(
        "INSERT INTO price_daily (stock_id, trade_date, open, high, low, close, volume)"
        " VALUES (%s, %s, 100, 100, 100, 100, 1)",
        (TEST_STOCK_ID, date(2023, 12, 20)),
    )

    master.refine_delisted_at(cur)

    assert master.get_stock(cur, TEST_STOCK_ID).delisted_at is None


def test_조정계수는_이벤트_이전_가격에만_붙는다(cur):
    from decimal import Decimal

    from common.db.prices import apply_adj_factor, reset_adj_factor

    master.upsert_stocks(cur, [sample_stock()])
    cur.executemany(
        "INSERT INTO price_daily (stock_id, trade_date, open, high, low, close, volume)"
        " VALUES (%s, %s, 100, 100, 100, 100, 1)",
        [
            (TEST_STOCK_ID, date(2024, 11, 8)),
            (TEST_STOCK_ID, date(2024, 11, 11)),
            (TEST_STOCK_ID, date(2024, 11, 12)),
        ],
    )
    reset_adj_factor(cur, [TEST_STOCK_ID])

    # 50:1 감자. 이전 가격에 50 을 곱해야 이어진다
    apply_adj_factor(cur, TEST_STOCK_ID, date(2024, 11, 11), Decimal("0.02"))

    cur.execute(
        "SELECT trade_date, adj_factor FROM price_daily WHERE stock_id = %s"
        " ORDER BY trade_date",
        (TEST_STOCK_ID,),
    )
    assert cur.fetchall() == [
        (date(2024, 11, 8), Decimal("50.0000000000")),
        (date(2024, 11, 11), Decimal("1.0000000000")),
        (date(2024, 11, 12), Decimal("1.0000000000")),
    ]


def test_이벤트가_둘이면_조정계수가_누적된다(cur):
    from decimal import Decimal

    from common.db.prices import apply_adj_factor, reset_adj_factor

    master.upsert_stocks(cur, [sample_stock()])
    cur.executemany(
        "INSERT INTO price_daily (stock_id, trade_date, open, high, low, close, volume)"
        " VALUES (%s, %s, 100, 100, 100, 100, 1)",
        [(TEST_STOCK_ID, date(2024, 1, 5)), (TEST_STOCK_ID, date(2024, 6, 5))],
    )
    reset_adj_factor(cur, [TEST_STOCK_ID])

    apply_adj_factor(cur, TEST_STOCK_ID, date(2024, 3, 1), Decimal("0.5"))
    apply_adj_factor(cur, TEST_STOCK_ID, date(2024, 9, 1), Decimal("0.2"))

    cur.execute(
        "SELECT adj_factor FROM price_daily WHERE stock_id = %s AND trade_date = %s",
        (TEST_STOCK_ID, date(2024, 1, 5)),
    )
    # 두 이벤트 모두 이후에 있으므로 2 x 5 = 10
    assert cur.fetchone()[0] == Decimal("10.0000000000")
