# 스윙 엔진 테스트. 앞쪽은 DB 없이 돌고 뒤쪽은 서버에서만 돈다

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from common.broker.base import OrderResult
from common.broker.mock import MockBroker
from common.db.filters import add_filter
from common.db.orders import apply_result, list_open_orders, record_pending
from common.db.signals import pending_signals, record_signal
from common.risk import RiskManager
from common.strategy.base import Context, EntryIntent, ExitIntent, Strategy
from common.types import (
    Balance,
    OrderType,
    Position,
    Quote,
    Regime,
    Side,
    Signal,
)
from engines.swing.engine import SwingEngine, _hhmm

SEOUL = ZoneInfo("Asia/Seoul")

PROCESS = "engine-swing-test"
STRATEGY = "swing-test"
ACCOUNT = "paper"

PARAMS = {
    "process_name": PROCESS,
    "account_id": ACCOUNT,
    "tick_sec": 10,
    "heartbeat_sec": 60,
    "submit_time": "08:30",
    "cancel_time": "15:30",
    "snapshot_time": "15:40",
    "plan_time": "19:00",
    "track_from": "09:00",
    "fill_poll_sec": 60,
    "plan_retry_min": 20,
    "plan_retry_max": 6,
    "position_tolerance": 0,
}

RISK = {
    "regime_allocation": {"danger": 0.3, "neutral": 0.7, "safe": 1.0},
    "max_position_size": 1000000,
    "max_weight_per_stock": 0.1,
    "max_positions": 10,
    "daily_loss_limit": 0.03,
}


class StubStrategy(Strategy):
    """전략 내용은 이 테스트의 관심사가 아니다. 미리 정한 것을 돌려준다."""

    name = STRATEGY

    def __init__(self, entries=None, exits=None) -> None:
        self.entries = entries or []
        self.exits = exits or {}

    def scan(self, ctx: Context) -> list[EntryIntent]:
        return list(self.entries)

    def manage(self, ctx: Context, position: Position) -> ExitIntent | None:
        return self.exits.get(position.stock_id)


class StubFeed:
    """`Context.feed` 자리. 엔진이 실제로 부르는 것만 갖췄다."""

    def __init__(self, now: datetime, regime: Regime = Regime.NEUTRAL) -> None:
        self._now = now
        self._regime = regime

    def now(self) -> datetime:
        return self._now

    def get_regime(self) -> Regime:
        return self._regime

    def get_quote(self, stock_id: str) -> Quote:
        return Quote(stock_id, self._now, Decimal(1000), None, None, 0)

    def get_universe(self) -> list[str]:
        return []


def balance(total: str = "10000000", available: str = "10000000") -> Balance:
    return Balance(
        account_id=ACCOUNT,
        deposit=Decimal(available),
        available=Decimal(available),
        eval_amount=Decimal(0),
        total_asset=Decimal(total),
    )


def build(
    *,
    conn=None,
    broker=None,
    strategy=None,
    now: datetime | None = None,
    allocation: Decimal = Decimal(0),
) -> SwingEngine:
    moment = now or datetime(2026, 9, 1, 8, 30, tzinfo=SEOUL)
    return SwingEngine(
        conn=conn,
        feed=StubFeed(moment),
        broker=broker or MockBroker(balance=balance()),
        strategy=strategy or StubStrategy(),
        risk=RiskManager(RISK),
        strategy_params={},
        params=dict(PARAMS),
        allocation=allocation,
    )


def signal(stock_id: str, side: Side, signal_id: int = 1) -> Signal:
    return Signal(
        signal_id=signal_id,
        stock_id=stock_id,
        strategy=STRATEGY,
        side=side,
        strength=Decimal(5),
        payload=None,
        regime_at="neutral",
        created_at=datetime.now(UTC),
    )


def context(engine: SwingEngine, positions: list[Position], bal: Balance) -> Context:
    return Context(
        feed=engine.feed,
        account_id=ACCOUNT,
        params={},
        positions={p.stock_id: p for p in positions},
        balance=bal,
    )


# --- DB 없이 도는 것들 --------------------------------------------------------


def test_설정의_시각_문자열을_읽는다():
    assert _hhmm("08:30") == time(8, 30)
    assert _hhmm("19:00") == time(19, 0)


def test_now_는_한국_시각이다():
    """UTC 로 읽으면 09:00 이전 한국 시각이 전날로 밀린다 (CLAUDE.md 5)."""
    engine = build(now=datetime(2026, 9, 1, 0, 30, tzinfo=UTC))
    assert engine.now().date() == date(2026, 9, 1)
    assert engine.now().hour == 9


def test_매도_수량은_지금_보유_수량이다():
    """계획에 수량을 담지 않으므로 제출 시점의 잔고에서 되찾는다."""
    engine = build()
    position = Position(ACCOUNT, "KRX:005930", 7, Decimal(1000))
    ctx = context(engine, [position], balance())

    quantity = engine._quantity_for(
        signal("KRX:005930", Side.SELL), ctx, Regime.NEUTRAL
    )

    assert quantity == 7


def test_이미_없는_종목의_매도는_주문하지_않는다():
    engine = build()
    ctx = context(engine, [], balance())

    assert (
        engine._quantity_for(signal("KRX:005930", Side.SELL), ctx, Regime.NEUTRAL)
        is None
    )


def test_매수_수량은_제출_시점_잔고로_정해진다():
    """`RiskManager._budget` 이 available 로 상한을 잡는다."""
    engine = build()
    ctx = context(engine, [], balance())

    # 총자산 1000만 × max_weight 0.1 = 100만, 주가 1000 → 1000주
    assert (
        engine._quantity_for(signal("KRX:005930", Side.BUY), ctx, Regime.NEUTRAL)
        == 1000
    )


def test_주문가능금액이_모자라면_수량이_줄어든다():
    """갭상승으로 현금이 모자란 상황. 백테스트 `_buy` 의 감량과 같은 자리다."""
    engine = build()
    ctx = context(engine, [], balance(total="10000000", available="300000"))

    assert (
        engine._quantity_for(signal("KRX:005930", Side.BUY), ctx, Regime.NEUTRAL) == 300
    )


def test_진입이_막혀_있으면_매수를_내지_않는다():
    engine = build()
    engine.halt_entry = True
    ctx = context(engine, [], balance())

    assert (
        engine._quantity_for(signal("KRX:005930", Side.BUY), ctx, Regime.NEUTRAL)
        is None
    )


def test_진입이_막혀도_매도는_낸다():
    """청산은 진입차단과 무관하게 돈다 (INTERFACES.md 4.1)."""
    engine = build()
    engine.halt_entry = True
    position = Position(ACCOUNT, "KRX:005930", 7, Decimal(1000))
    ctx = context(engine, [position], balance())

    assert (
        engine._quantity_for(signal("KRX:005930", Side.SELL), ctx, Regime.NEUTRAL) == 7
    )


def test_allocation_이_총자산에_상한을_건다():
    """0 이면 브로커 잔고 그대로, 양수면 그 금액이 상한이다."""
    unlimited = build(broker=MockBroker(balance=balance(total="10000000")))
    assert unlimited._context(positions=[]).balance.total_asset == Decimal(10000000)

    capped = build(
        broker=MockBroker(balance=balance(total="10000000")),
        allocation=Decimal(2000000),
    )
    ctx = capped._context(positions=[])
    assert ctx.balance.total_asset == Decimal(2000000)
    # 주문가능금액은 건드리지 않는다. 국면 배분이 이미 총 투입을 제한한다
    assert ctx.balance.available == Decimal(10000000)


def test_일봉이_없으면_다시_볼_시각을_잡는다():
    engine = build()
    now = datetime(2026, 9, 1, 19, 0, tzinfo=SEOUL)

    engine._defer_plan(now)

    assert engine.plan_attempts == 1
    assert engine.retry_at["plan"] == now + timedelta(minutes=20)
    assert "plan" not in engine.done


def test_끝까지_기다려도_없으면_그날을_건너뛴다(monkeypatch):
    """'데이터가 없어서 안 샀다' 는 안전한 실패다."""
    engine = build()
    events = []
    monkeypatch.setattr(engine, "_event", lambda *a, **k: events.append(a))
    now = datetime(2026, 9, 1, 19, 0, tzinfo=SEOUL)

    for _ in range(PARAMS["plan_retry_max"] + 1):
        engine._defer_plan(now)

    assert engine.done["plan"] == date(2026, 9, 1)
    assert engine.plan_attempts == 0
    assert "plan" not in engine.retry_at
    assert events


def test_모르는_명령은_예외다():
    engine = build()

    class Cmd:
        action = "self_destruct"
        params = None

    with pytest.raises(ValueError, match="모르는 명령"):
        engine._handle(Cmd())


def test_주기_판정은_처음에_참이다():
    now = datetime(2026, 9, 1, 9, 0, tzinfo=SEOUL)
    assert SwingEngine._elapsed(None, now, 60) is True
    assert SwingEngine._elapsed(now - timedelta(seconds=30), now, 60) is False
    assert SwingEngine._elapsed(now - timedelta(seconds=60), now, 60) is True


# --- DB 가 필요한 것들 (서버에서만 돈다) ---------------------------------------


@pytest.fixture
def stocks(engine_conn):
    with engine_conn.cursor() as cur:
        cur.execute("SELECT stock_id FROM stock ORDER BY stock_id LIMIT 2")
        rows = [row[0] for row in cur.fetchall()]
    engine_conn.rollback()
    if len(rows) < 2:
        pytest.skip("종목 마스터가 비어 있습니다")
    return rows


@pytest.fixture
def paper(engine_conn):
    with engine_conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM account WHERE account_id = %s AND is_paper", (ACCOUNT,)
        )
        found = cur.fetchone()
    engine_conn.rollback()
    if found is None:
        pytest.skip(f"모의투자 계좌 {ACCOUNT} 가 없습니다")


def test_잔고_불일치는_진입만_막는다(engine_conn, paper, stocks):
    """청산은 계속 돈다. 손절이 원장 불일치로 묶이면 그쪽이 더 위험하다."""
    broker = MockBroker(
        balance=balance(),
        positions=[Position(ACCOUNT, stocks[0], 10, Decimal(1000))],
    )
    engine = build(conn=engine_conn, broker=broker)

    # 캐시를 채워두고 증권사가 다른 수량을 주게 만든다
    engine._sync_positions(cold_start_ok=True)
    assert engine.halt_entry is False

    broker._positions = [Position(ACCOUNT, stocks[0], 3, Decimal(1000))]
    engine._sync_positions()

    assert engine.halt_entry is True
    with engine_conn.cursor() as cur:
        cur.execute(
            "SELECT quantity FROM position WHERE account_id = %s AND stock_id = %s",
            (ACCOUNT, stocks[0]),
        )
        assert cur.fetchone()[0] == 3  # 증권사 값으로 덮어쓴다
    engine_conn.rollback()


def test_빈_캐시로_시작하는_것은_불일치가_아니다(engine_conn, paper, stocks):
    with engine_conn.cursor() as cur:
        cur.execute("DELETE FROM position WHERE account_id = %s", (ACCOUNT,))
    engine_conn.commit()

    broker = MockBroker(
        balance=balance(),
        positions=[Position(ACCOUNT, stocks[0], 10, Decimal(1000))],
    )
    engine = build(conn=engine_conn, broker=broker)
    engine.recover()

    assert engine.halt_entry is False


def test_계획을_남긴다(engine_conn, paper, stocks):
    strategy = StubStrategy(
        entries=[
            EntryIntent(stocks[0], Side.BUY, Decimal(3), payload={"reason": "breakout"})
        ]
    )
    engine = build(
        conn=engine_conn, broker=MockBroker(balance=balance()), strategy=strategy
    )

    assert engine._plan_entries(context(engine, [], balance()), Regime.NEUTRAL) == 1

    with engine_conn.cursor() as cur:
        rows = pending_signals(
            cur, STRATEGY, datetime.now(SEOUL) - timedelta(minutes=5)
        )
    engine_conn.rollback()
    assert [(s.stock_id, s.side) for s in rows] == [(stocks[0], Side.BUY)]


def test_부분_청산_의도는_계획에_담지_않는다(engine_conn, paper, stocks):
    """수량을 담지 않으므로 표현할 수 없다. 조용히 전량 파는 것보다 안전하다."""
    position = Position(ACCOUNT, stocks[0], 10, Decimal(1000))
    strategy = StubStrategy(exits={stocks[0]: ExitIntent(stocks[0], 4, "stop")})
    engine = build(conn=engine_conn, strategy=strategy)
    ctx = context(engine, [position], balance())

    assert engine._plan_exits(ctx, Regime.NEUTRAL) == 0

    with engine_conn.cursor() as cur:
        rows = pending_signals(
            cur, STRATEGY, datetime.now(SEOUL) - timedelta(minutes=5)
        )
        cur.execute(
            "SELECT count(*) FROM event_log WHERE process_name = %s AND level = 'ERROR'",
            (PROCESS,),
        )
        errors = cur.fetchone()[0]
    engine_conn.rollback()
    assert rows == []
    assert errors == 1


def test_전량_청산_의도는_계획에_담는다(engine_conn, paper, stocks):
    position = Position(ACCOUNT, stocks[0], 10, Decimal(1000))
    strategy = StubStrategy(exits={stocks[0]: ExitIntent(stocks[0], 10, "stop")})
    engine = build(conn=engine_conn, strategy=strategy)

    assert (
        engine._plan_exits(context(engine, [position], balance()), Regime.NEUTRAL) == 1
    )

    with engine_conn.cursor() as cur:
        [row] = pending_signals(
            cur, STRATEGY, datetime.now(SEOUL) - timedelta(minutes=5)
        )
    engine_conn.rollback()
    assert (row.side, row.payload) == (Side.SELL, {"reason": "stop"})


def test_제출하면_계획이_소비되고_주문이_남는다(engine_conn, paper, stocks):
    """소비와 기록이 **같은 커밋**이라 재시작해도 두 번 나가지 않는다."""
    with engine_conn.cursor() as cur:
        cur.execute("DELETE FROM position WHERE account_id = %s", (ACCOUNT,))
    engine_conn.commit()

    with engine_conn.cursor() as cur:
        signal_id = record_signal(
            cur,
            stock_id=stocks[0],
            strategy=STRATEGY,
            side=Side.BUY,
            strength=Decimal(3),
        )
    engine_conn.commit()

    engine = build(conn=engine_conn, broker=MockBroker(balance=balance()))
    ctx = context(engine, [], balance())
    engine._submit_one(signal(stocks[0], Side.BUY, signal_id), ctx, Regime.NEUTRAL)

    with engine_conn.cursor() as cur:
        cur.execute("SELECT consumed_at FROM signal WHERE signal_id = %s", (signal_id,))
        consumed = cur.fetchone()[0]
        cur.execute(
            "SELECT signal_id FROM order_request WHERE account_id = %s"
            " ORDER BY order_id DESC LIMIT 1",
            (ACCOUNT,),
        )
        linked = cur.fetchone()[0]
    engine_conn.rollback()

    assert consumed is not None
    assert linked == signal_id


def test_거부된_계획은_주문_없이_소비된다(engine_conn, paper, stocks):
    with engine_conn.cursor() as cur:
        signal_id = record_signal(
            cur, stock_id=stocks[0], strategy=STRATEGY, side=Side.SELL
        )
    engine_conn.commit()

    engine = build(conn=engine_conn)
    before = len(list_open_orders_of(engine_conn))

    # 보유하지 않은 종목의 매도다. 계획만 닫힌다
    engine._submit_one(
        signal(stocks[0], Side.SELL, signal_id),
        context(engine, [], balance()),
        Regime.NEUTRAL,
    )

    with engine_conn.cursor() as cur:
        cur.execute("SELECT consumed_at FROM signal WHERE signal_id = %s", (signal_id,))
        assert cur.fetchone()[0] is not None
    engine_conn.rollback()
    assert len(list_open_orders_of(engine_conn)) == before


def list_open_orders_of(conn) -> list:
    with conn.cursor() as cur:
        orders = list_open_orders(cur, ACCOUNT)
    conn.rollback()
    return orders


def test_명령을_처리하고_닫는다(engine_conn, paper):
    with engine_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO command (target, action) VALUES (%s, 'halt_entry')"
            " RETURNING command_id",
            (PROCESS,),
        )
        command_id = cur.fetchone()[0]
    engine_conn.commit()

    engine = build(conn=engine_conn)
    engine._poll_commands()

    assert engine.halt_entry is True
    with engine_conn.cursor() as cur:
        cur.execute("SELECT status FROM command WHERE command_id = %s", (command_id,))
        assert cur.fetchone()[0] == "done"
    engine_conn.rollback()


def test_정지_명령을_받으면_루프를_빠져나간다(engine_conn, paper):
    with engine_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO command (target, action) VALUES (%s, 'stop')", (PROCESS,)
        )
    engine_conn.commit()

    engine = build(conn=engine_conn)
    engine.tick(datetime(2026, 9, 1, 19, 30, tzinfo=SEOUL))

    assert engine.stopping is True
    # 정지가 걸렸으면 시간표대로 새 주문을 내지 않는다
    assert engine.done == {}


def test_스냅샷을_남긴다(engine_conn, paper):
    with engine_conn.cursor() as cur:
        cur.execute("DELETE FROM position WHERE account_id = %s", (ACCOUNT,))
    engine_conn.commit()

    engine = build(conn=engine_conn, broker=MockBroker(balance=balance(), positions=[]))
    engine._snapshot(datetime(1999, 1, 6, 15, 40, tzinfo=SEOUL))

    with engine_conn.cursor() as cur:
        cur.execute(
            "SELECT total_asset FROM daily_pnl WHERE account_id = %s AND trade_date = %s",
            (ACCOUNT, date(1999, 1, 6)),
        )
        assert cur.fetchone()[0] == Decimal(10000000)
    engine_conn.rollback()


def test_제외_목록의_종목은_계획에_안_들어간다(engine_conn, paper, stocks):
    """필터는 운영 판단이라 엔진이 본다. 전략은 필터를 모른다."""
    with engine_conn.cursor() as cur:
        add_filter(
            cur,
            stock_id=stocks[0],
            strategy=STRATEGY,
            filter_type="block",
            reason="악재",
        )
    engine_conn.commit()

    strategy = StubStrategy(
        entries=[
            EntryIntent(stocks[0], Side.BUY, Decimal(3)),
            EntryIntent(stocks[1], Side.BUY, Decimal(2)),
        ]
    )
    engine = build(conn=engine_conn, strategy=strategy)

    assert engine._plan_entries(context(engine, [], balance()), Regime.NEUTRAL) == 1

    with engine_conn.cursor() as cur:
        rows = pending_signals(
            cur, STRATEGY, datetime.now(SEOUL) - timedelta(minutes=5)
        )
    engine_conn.rollback()
    assert [s.stock_id for s in rows] == [stocks[1]]


def test_제외_목록은_청산을_막지_않는다(engine_conn, paper, stocks):
    """들고 있는 종목을 제외 목록에 넣었다고 팔지 못하면 갇힌다."""
    with engine_conn.cursor() as cur:
        add_filter(cur, stock_id=stocks[0], strategy=STRATEGY, filter_type="block")
    engine_conn.commit()

    position = Position(ACCOUNT, stocks[0], 10, Decimal(1000))
    strategy = StubStrategy(exits={stocks[0]: ExitIntent(stocks[0], 10, "stop")})
    engine = build(conn=engine_conn, strategy=strategy)

    assert (
        engine._plan_exits(context(engine, [position], balance()), Regime.NEUTRAL) == 1
    )


def test_사라진_주문_때문에_기동이_죽지_않는다(engine_conn, paper, stocks):
    """**2026-09-03 실측.** 취소된 주문은 어느 목록에도 안 남는다.

    체결 목록도 당일 것만 주므로 날이 바뀌면 어제 주문을 조회할 수 없다.
    둘 다 `get_order_status` 가 `PermanentError` 를 던진다.

    `recover()` 는 `run()` 의 예외 처리 밖이라, 여기서 예외가 올라가면
    **엔진이 시작하자마자 죽고** systemd 가 다섯 번 뒤 포기한다.
    """
    with engine_conn.cursor() as cur:
        record_pending(
            cur,
            client_order_id="TEST-VANISHED",
            account_id=ACCOUNT,
            stock_id=stocks[0],
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=1,
        )
        apply_result(
            cur,
            OrderResult(
                client_order_id="TEST-VANISHED",
                broker_order_no="0013842",
                status="submitted",
                filled_qty=0,
                avg_fill_price=None,
            ),
        )
    engine_conn.commit()

    # 목은 이 주문번호를 모른다. 실제 키움과 같은 예외를 던진다
    engine = build(conn=engine_conn, broker=MockBroker(balance=balance(), positions=[]))
    engine.recover()

    with engine_conn.cursor() as cur:
        cur.execute(
            "SELECT status, error_message FROM order_request"
            " WHERE client_order_id = 'TEST-VANISHED'"
        )
        status, message = cur.fetchone()
    engine_conn.rollback()

    assert status == "cancelled"  # 다시 대조하지 않도록 닫는다
    assert "사라졌다" in message


def test_사라진_주문은_진입을_막지_않는다(engine_conn, paper, stocks):
    """주문 행 하나 때문에 엔진을 못 띄우면 안 된다.

    보유 수량은 브로커 잔고가 정본이라 `_sync_positions` 가 바로 뒤에
    맞추고, 어긋나면 그때 진입을 막는다.
    """
    with engine_conn.cursor() as cur:
        cur.execute("DELETE FROM position WHERE account_id = %s", (ACCOUNT,))
        record_pending(
            cur,
            client_order_id="TEST-VANISHED-2",
            account_id=ACCOUNT,
            stock_id=stocks[0],
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=1,
        )
        apply_result(
            cur,
            OrderResult(
                client_order_id="TEST-VANISHED-2",
                broker_order_no="0099999",
                status="submitted",
                filled_qty=0,
                avg_fill_price=None,
            ),
        )
    engine_conn.commit()

    engine = build(conn=engine_conn, broker=MockBroker(balance=balance(), positions=[]))
    engine.recover()

    assert engine.halt_entry is False


def test_체결량은_사라져도_줄지_않는다(engine_conn, paper, stocks):
    """부분체결된 주문이 사라져도 체결 기록을 잃으면 안 된다.

    `apply_result` 의 GREATEST 가 막는다.
    """
    with engine_conn.cursor() as cur:
        record_pending(
            cur,
            client_order_id="TEST-VANISHED-3",
            account_id=ACCOUNT,
            stock_id=stocks[0],
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=10,
        )
        apply_result(
            cur,
            OrderResult(
                client_order_id="TEST-VANISHED-3",
                broker_order_no="0088888",
                status="partial",
                filled_qty=4,
                avg_fill_price=Decimal(1000),
            ),
        )
    engine_conn.commit()

    engine = build(conn=engine_conn, broker=MockBroker(balance=balance(), positions=[]))
    engine.recover()

    with engine_conn.cursor() as cur:
        cur.execute(
            "SELECT status, filled_qty, avg_fill_price FROM order_request"
            " WHERE client_order_id = 'TEST-VANISHED-3'"
        )
        status, filled, price = cur.fetchone()
    engine_conn.rollback()

    assert status == "cancelled"
    assert filled == 4  # 줄지 않는다
    assert price == Decimal(1000)  # 체결가도 지킨다


def test_오늘_일봉을_요구하지_않는다(engine_conn, paper):
    """**KRX 는 D일 데이터를 D+1 에 공개한다.**

    19:00 수집기가 채우는 것은 어제 것이다. 오늘을 요구하면 엔진이 매일
    판단을 건너뛴다 (2026-09-03 확인).
    """
    engine = build(conn=engine_conn)
    with engine_conn.cursor() as cur:
        cur.execute("SELECT MAX(trade_date) FROM price_daily")
        latest = cur.fetchone()[0]
    engine_conn.rollback()
    if latest is None:
        pytest.skip("일봉이 없습니다")

    # 어제 데이터로 오늘 판단하는 것이 정상이다
    assert engine._daily_loaded(latest + timedelta(days=1)) is True


def test_며칠_묵으면_그날을_건너뛴다(engine_conn, paper):
    """수집기가 멈춘 것을 잡는다. 오래된 데이터로 판단하는 것보다 낫다."""
    engine = build(conn=engine_conn)
    with engine_conn.cursor() as cur:
        cur.execute("SELECT MAX(trade_date) FROM price_daily")
        latest = cur.fetchone()[0]
    engine_conn.rollback()
    if latest is None:
        pytest.skip("일봉이 없습니다")

    stale = engine.params["plan_max_stale_days"] + 1
    assert engine._daily_loaded(latest + timedelta(days=stale)) is False
