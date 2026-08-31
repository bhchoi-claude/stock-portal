# 엔진이 쓰는 DB 접근 모듈 테스트. 전부 롤백되므로 DB 에 흔적이 없다

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from common.db.commands import ack, complete, pending_commands
from common.db.filters import add_filter, blocked_stock_ids, list_filters, remove_filter
from common.db.pnl import snapshot
from common.db.positions import list_positions, sync_positions
from common.db.signals import consume, pending_signals, record_signal
from common.types import Balance, Position, Side

SEOUL = ZoneInfo("Asia/Seoul")

# 실제 매매와 겹치지 않도록 쓰는 값들
STRATEGY = "swing-test"
PROCESS = "engine-swing-test"
ACCOUNT = "paper"


@pytest.fixture
def stocks(cur):
    """DB 에 실재하는 종목 둘. `signal`·`position` 이 FK 로 요구한다."""
    cur.execute("SELECT stock_id FROM stock ORDER BY stock_id LIMIT 2")
    rows = [row[0] for row in cur.fetchall()]
    if len(rows) < 2:
        pytest.skip("종목 마스터가 비어 있습니다")
    return rows


@pytest.fixture
def account(cur):
    """모의투자 계좌만 쓴다 (CLAUDE.md 실계좌 보호)."""
    cur.execute(
        "SELECT account_id FROM account WHERE account_id = %s AND is_paper", (ACCOUNT,)
    )
    if cur.fetchone() is None:
        pytest.skip(f"모의투자 계좌 {ACCOUNT} 가 없습니다")
    return ACCOUNT


# --- signal ------------------------------------------------------------------


def test_계획을_남기고_다시_읽는다(cur, stocks):
    since = datetime.now(SEOUL) - timedelta(hours=1)
    signal_id = record_signal(
        cur,
        stock_id=stocks[0],
        strategy=STRATEGY,
        side=Side.BUY,
        strength=Decimal("3.5"),
        payload={"reason": "breakout"},
        regime_at="neutral",
    )

    [signal] = pending_signals(cur, STRATEGY, since)
    assert signal.signal_id == signal_id
    assert signal.stock_id == stocks[0]
    assert signal.side is Side.BUY
    assert signal.strength == Decimal("3.5")
    assert signal.payload == {"reason": "breakout"}


def test_소비한_계획은_다시_안_나온다(cur, stocks):
    since = datetime.now(SEOUL) - timedelta(hours=1)
    signal_id = record_signal(
        cur, stock_id=stocks[0], strategy=STRATEGY, side=Side.SELL
    )
    assert len(pending_signals(cur, STRATEGY, since)) == 1

    consume(cur, signal_id)
    assert pending_signals(cur, STRATEGY, since) == []


def test_since_보다_오래된_계획은_안_나온다(cur, stocks):
    """계획은 하루짜리다. 아침을 한 번 걸렀다고 이틀 전 계획을 내면 안 된다."""
    record_signal(cur, stock_id=stocks[0], strategy=STRATEGY, side=Side.BUY)
    future = datetime.now(SEOUL) + timedelta(hours=1)
    assert pending_signals(cur, STRATEGY, future) == []


def test_다른_전략의_계획은_안_섞인다(cur, stocks):
    since = datetime.now(SEOUL) - timedelta(hours=1)
    record_signal(cur, stock_id=stocks[0], strategy=STRATEGY, side=Side.BUY)
    record_signal(cur, stock_id=stocks[1], strategy=STRATEGY + "-other", side=Side.BUY)
    assert len(pending_signals(cur, STRATEGY, since)) == 1


def test_consume_는_두_번_찍히지_않는다(cur, stocks):
    signal_id = record_signal(cur, stock_id=stocks[0], strategy=STRATEGY, side=Side.BUY)
    consume(cur, signal_id)
    cur.execute("SELECT consumed_at FROM signal WHERE signal_id = %s", (signal_id,))
    first = cur.fetchone()[0]

    consume(cur, signal_id)
    cur.execute("SELECT consumed_at FROM signal WHERE signal_id = %s", (signal_id,))
    assert cur.fetchone()[0] == first


# --- command -----------------------------------------------------------------


def test_명령을_폴링하고_닫는다(cur):
    cur.execute(
        "INSERT INTO command (target, action, issued_by)"
        " VALUES (%s, 'halt_entry', 'test') RETURNING command_id",
        (PROCESS,),
    )
    command_id = cur.fetchone()[0]

    [command] = [
        c for c in pending_commands(cur, PROCESS) if c.command_id == command_id
    ]
    assert command.action == "halt_entry"

    ack(cur, command_id)
    assert command_id not in {c.command_id for c in pending_commands(cur, PROCESS)}

    complete(cur, command_id, ok=True, result="차단했습니다")
    cur.execute(
        "SELECT status, result FROM command WHERE command_id = %s", (command_id,)
    )
    assert cur.fetchone() == ("done", "차단했습니다")


def test_all_로_보낸_명령도_받는다(cur):
    cur.execute(
        "INSERT INTO command (target, action) VALUES ('all', 'stop')"
        " RETURNING command_id"
    )
    command_id = cur.fetchone()[0]
    assert command_id in {c.command_id for c in pending_commands(cur, PROCESS)}


def test_남의_명령은_안_집는다(cur):
    cur.execute(
        "INSERT INTO command (target, action) VALUES ('engine-daytrade', 'stop')"
        " RETURNING command_id"
    )
    command_id = cur.fetchone()[0]
    assert command_id not in {c.command_id for c in pending_commands(cur, PROCESS)}


def test_ack_는_두_번_안_먹는다(cur):
    """처리 중에 죽었을 때 다음 폴링이 같은 명령을 또 집으면 안 된다."""
    cur.execute(
        "INSERT INTO command (target, action) VALUES (%s, 'stop') RETURNING command_id",
        (PROCESS,),
    )
    command_id = cur.fetchone()[0]

    ack(cur, command_id)
    cur.execute("SELECT acked_at FROM command WHERE command_id = %s", (command_id,))
    first = cur.fetchone()[0]

    ack(cur, command_id)
    cur.execute("SELECT acked_at FROM command WHERE command_id = %s", (command_id,))
    assert cur.fetchone()[0] == first


# --- position ----------------------------------------------------------------


def held(account_id: str, stock_id: str, quantity: int) -> Position:
    return Position(account_id, stock_id, quantity, Decimal(1000))


def test_잔고를_받아_새로_적재하면_불일치로_보고한다(cur, account, stocks):
    cur.execute("DELETE FROM position WHERE account_id = %s", (account,))

    mismatches = sync_positions(cur, account, [held(account, stocks[0], 10)])

    assert [(m.stock_id, m.db_quantity, m.broker_quantity) for m in mismatches] == [
        (stocks[0], 0, 10)
    ]
    assert [(p.stock_id, p.quantity) for p in list_positions(cur, account)] == [
        (stocks[0], 10)
    ]


def test_같으면_불일치가_없다(cur, account, stocks):
    cur.execute("DELETE FROM position WHERE account_id = %s", (account,))
    sync_positions(cur, account, [held(account, stocks[0], 10)])

    assert sync_positions(cur, account, [held(account, stocks[0], 10)]) == []


def test_증권사에_없는_종목은_지운다(cur, account, stocks):
    cur.execute("DELETE FROM position WHERE account_id = %s", (account,))
    sync_positions(cur, account, [held(account, stocks[0], 10)])

    mismatches = sync_positions(cur, account, [held(account, stocks[1], 5)])

    assert {m.stock_id for m in mismatches} == {stocks[0], stocks[1]}
    assert [p.stock_id for p in list_positions(cur, account)] == [stocks[1]]


def test_수량이_다르면_증권사_값으로_덮어쓴다(cur, account, stocks):
    """증권사 잔고가 정본이고 이 테이블은 캐시다 (SCHEMA.md 5장)."""
    cur.execute("DELETE FROM position WHERE account_id = %s", (account,))
    sync_positions(cur, account, [held(account, stocks[0], 10)])

    [mismatch] = sync_positions(cur, account, [held(account, stocks[0], 7)])

    assert (mismatch.db_quantity, mismatch.broker_quantity) == (10, 7)
    assert list_positions(cur, account)[0].quantity == 7


def test_빈_잔고를_받으면_전부_지운다(cur, account, stocks):
    cur.execute("DELETE FROM position WHERE account_id = %s", (account,))
    sync_positions(cur, account, [held(account, stocks[0], 10)])

    assert sync_positions(cur, account, []) != []
    assert list_positions(cur, account) == []


# --- daily_pnl ---------------------------------------------------------------


def test_스냅샷은_평가손익을_매입금액에서_뺀다(cur, account, stocks):
    day = date(1999, 1, 4)  # 실제 운영 기록과 겹치지 않는 날
    balance = Balance(
        account_id=account,
        deposit=Decimal(1000),
        available=Decimal(900),
        eval_amount=Decimal(1500),
        total_asset=Decimal(2500),
    )
    positions = [Position(account, stocks[0], 10, Decimal(120))]

    snapshot(cur, trade_date=day, balance=balance, positions=positions)

    cur.execute(
        "SELECT deposit, eval_amount, total_asset, unrealized_pnl, realized_pnl"
        " FROM daily_pnl WHERE account_id = %s AND trade_date = %s",
        (account, day),
    )
    deposit, eval_amount, total, unrealized, realized = cur.fetchone()
    assert (deposit, eval_amount, total) == (
        Decimal(1000),
        Decimal(1500),
        Decimal(2500),
    )
    # 평가 1500 − 매입 1200
    assert unrealized == Decimal(300)
    # 원가를 모르므로 채우지 않는다. 0 을 적지 않는다
    assert realized is None


def test_같은_날_다시_찍으면_덮어쓴다(cur, account):
    day = date(1999, 1, 5)

    def write(total: str) -> None:
        snapshot(
            cur,
            trade_date=day,
            balance=Balance(
                account_id=account,
                deposit=Decimal(0),
                available=Decimal(0),
                eval_amount=Decimal(0),
                total_asset=Decimal(total),
            ),
            positions=[],
        )

    write("100")
    write("200")

    cur.execute(
        "SELECT count(*), max(total_asset) FROM daily_pnl"
        " WHERE account_id = %s AND trade_date = %s",
        (account, day),
    )
    assert cur.fetchone() == (1, Decimal(200))


# --- stock_filter ------------------------------------------------------------


def test_제외_목록에_있는_종목을_돌려준다(cur, stocks):
    add_filter(cur, stock_id=stocks[0], strategy="swing", filter_type="block")

    assert stocks[0] in blocked_stock_ids(cur, "swing", date(2026, 9, 1))


def test_all_전략_제외는_모든_전략에_걸린다(cur, stocks):
    add_filter(cur, stock_id=stocks[0], strategy="all", filter_type="block")

    assert stocks[0] in blocked_stock_ids(cur, "swing", date(2026, 9, 1))
    assert stocks[0] in blocked_stock_ids(cur, "daytrade", date(2026, 9, 1))


def test_다른_전략의_제외는_안_걸린다(cur, stocks):
    add_filter(cur, stock_id=stocks[0], strategy="daytrade", filter_type="block")

    assert stocks[0] not in blocked_stock_ids(cur, "swing", date(2026, 9, 1))


def test_기한이_지난_제외는_안_걸린다(cur, stocks):
    add_filter(
        cur,
        stock_id=stocks[0],
        strategy="swing",
        filter_type="block",
        until_date=date(2026, 8, 31),
    )

    assert stocks[0] in blocked_stock_ids(cur, "swing", date(2026, 8, 31))
    assert stocks[0] not in blocked_stock_ids(cur, "swing", date(2026, 9, 1))


def test_allow_는_진입_차단에_안_섞인다(cur, stocks):
    """화이트리스트 모드는 아직 없다. block 만 본다."""
    add_filter(cur, stock_id=stocks[0], strategy="swing", filter_type="allow")

    assert stocks[0] not in blocked_stock_ids(cur, "swing", date(2026, 9, 1))


def test_목록을_넣고_지운다(cur, stocks):
    filter_id = add_filter(
        cur,
        stock_id=stocks[0],
        strategy="swing",
        filter_type="block",
        reason="악재",
    )

    [row] = [f for f in list_filters(cur) if f.filter_id == filter_id]
    assert (row.stock_id, row.filter_type, row.reason) == (stocks[0], "block", "악재")

    assert remove_filter(cur, filter_id) is True
    assert remove_filter(cur, filter_id) is False
