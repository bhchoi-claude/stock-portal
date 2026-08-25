# dataclass 필드가 마이그레이션 DDL 의 컬럼과 어긋나지 않는지 확인한다

import pathlib
import re
from dataclasses import fields

import pytest

from common.db.master import STOCK_COLUMNS
from common.db.models import Account, Exchange, Holiday, Source, Stock, StockStatus

# 실행 위치와 무관하게 읽는다
DDL_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "common/db/migrations/001_initial.sql"
)
DDL = DDL_PATH.read_text(encoding="utf-8")

CONSTRAINT_KEYWORDS = ("PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT")


def table_columns(table: str) -> list[str]:
    """CREATE TABLE 블록에서 컬럼명을 정의 순서대로 뽑는다."""
    match = re.search(rf"CREATE TABLE {table} \((.*?)\n\);", DDL, re.DOTALL)
    assert match, f"{table} 테이블 DDL 을 찾지 못했습니다"

    columns = []
    for raw in match.group(1).splitlines():
        line = raw.split("--")[0].strip()
        if not line or line.upper().startswith(CONSTRAINT_KEYWORDS):
            continue
        columns.append(line.split()[0])
    return columns


# dataclass 가 일부러 담지 않는 컬럼. DB 가 채우거나 이 계층이 쓰지 않는 것들이다.
CASES = [
    (Exchange, "exchange", set()),
    (Holiday, "exchange_holiday", set()),
    (Stock, "stock", {"updated_at"}),
    (StockStatus, "stock_status", set()),
    (Account, "account", set()),
    (Source, "source", {"source_id", "last_success_at", "created_at"}),
]


@pytest.mark.parametrize(
    "model, table, omitted", CASES, ids=lambda v: getattr(v, "__name__", "")
)
def test_필드가_컬럼과_일치한다(model, table, omitted):
    assert {f.name for f in fields(model)} == set(table_columns(table)) - omitted


def test_stock_columns_는_dataclass_필드_순서와_같다():
    # get_stock 이 Stock(*row) 로 만들기 때문에 순서가 어긋나면 값이 뒤섞인다
    assert STOCK_COLUMNS == tuple(f.name for f in fields(Stock))
