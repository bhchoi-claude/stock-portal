# DB 통합 테스트가 함께 쓰는 픽스처. 모든 행은 롤백된다

import psycopg
import pytest

from common.db.conn import load_database_url


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


@pytest.fixture
def db_conn():
    """실제로 커밋이 일어나는 커넥션. 끝나면 이 테스트가 만든 행만 지운다.

    `place_order` 는 주문을 내기 **전에** 기록을 커밋한다
    (`INTERFACES.md` 2.1). 롤백 픽스처로는 그 순서를 확인할 수 없다.

    지우는 범위를 `order_id` 수위표와 계좌로 좁힌다. 같은 DB 에서 엔진이
    동시에 돌면 그 행까지 지울 수 있으므로, 모의투자 계좌로만 돌린다.
    """
    try:
        url = load_database_url()
    except RuntimeError:
        pytest.skip("DATABASE_URL 이 없어 DB 통합 테스트를 건너뜁니다")

    conn = psycopg.connect(url)
    with conn.cursor() as c:
        c.execute("SELECT COALESCE(MAX(order_id), 0) FROM order_request")
        watermark = c.fetchone()[0]
    conn.commit()

    try:
        yield conn
    finally:
        with conn.cursor() as c:
            c.execute(
                "DELETE FROM execution WHERE order_id IN"
                " (SELECT order_id FROM order_request"
                "  WHERE order_id > %s AND account_id = 'paper')",
                (watermark,),
            )
            c.execute(
                "DELETE FROM order_request"
                " WHERE order_id > %s AND account_id = 'paper'",
                (watermark,),
            )
        conn.commit()
        conn.close()


@pytest.fixture
def read_conn():
    """읽기 전용 autocommit 커넥션. `LiveFeed` 가 요구하는 형태다.

    롤백 픽스처(`cur`)의 커넥션을 넘기면 격리가 깨지므로 따로 연다.
    """
    try:
        url = load_database_url()
    except RuntimeError:
        pytest.skip("DATABASE_URL 이 없어 DB 통합 테스트를 건너뜁니다")

    conn = psycopg.connect(url, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def engine_conn():
    """엔진 통합 테스트용. 커밋되는 커넥션을 주고 이 테스트가 만든 것만 지운다.

    `SwingEngine` 은 `transaction()` 으로 직접 커밋한다. 롤백 픽스처로는
    확인할 수 없다 — 주문 기록이 커밋돼야 한다는 것이 규격이기 때문이다
    (`INTERFACES.md` 2.1).

    지우는 범위를 좁힌다. 서버에서 진짜 엔진이 같은 DB 를 쓰고 있을 수
    있으므로, 테스트는 전용 `process_name` 과 전용 전략 이름을 쓴다.
    `position` 은 계좌를 공유하므로 **미리 떠놨다가 되돌린다.**
    """
    try:
        url = load_database_url()
    except RuntimeError:
        pytest.skip("DATABASE_URL 이 없어 DB 통합 테스트를 건너뜁니다")

    conn = psycopg.connect(url)
    with conn.cursor() as c:
        c.execute("SELECT COALESCE(MAX(order_id), 0) FROM order_request")
        watermark = c.fetchone()[0]
        c.execute(
            "SELECT stock_id, quantity, avg_price, currency, opened_at, synced_at"
            " FROM position WHERE account_id = 'paper'"
        )
        saved = c.fetchall()
    conn.commit()

    try:
        yield conn
    finally:
        with conn.cursor() as c:
            c.execute(
                "DELETE FROM execution WHERE order_id IN"
                " (SELECT order_id FROM order_request"
                "  WHERE order_id > %s AND account_id = 'paper')",
                (watermark,),
            )
            c.execute(
                "DELETE FROM order_request"
                " WHERE order_id > %s AND account_id = 'paper'",
                (watermark,),
            )
            c.execute("DELETE FROM signal WHERE strategy = 'swing-test'")
            c.execute("DELETE FROM command WHERE target = 'engine-swing-test'")
            c.execute("DELETE FROM heartbeat WHERE process_name = 'engine-swing-test'")
            c.execute("DELETE FROM event_log WHERE process_name = 'engine-swing-test'")
            c.execute(
                "DELETE FROM daily_pnl"
                " WHERE account_id = 'paper' AND trade_date < DATE '2000-01-01'"
            )
            c.execute("DELETE FROM position WHERE account_id = 'paper'")
            c.executemany(
                "INSERT INTO position (account_id, stock_id, quantity, avg_price,"
                " currency, opened_at, synced_at)"
                " VALUES ('paper', %s, %s, %s, %s, %s, %s)",
                saved,
            )
        conn.commit()
        conn.close()
