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
