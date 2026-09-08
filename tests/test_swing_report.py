# 매매 요약. 서식과 경보 등급을 고정한다

from datetime import UTC, date, datetime
from decimal import Decimal

from common.db.events import EventRow
from common.db.heartbeat import ProcessState
from common.db.orders import OrderView
from common.db.pnl import PnlRow
from common.db.positions import PositionView
from common.db.regime import RegimeRow
from engines.swing.report import Report, level, render, stale_seconds

DAY = date(2026, 9, 9)
NOW = datetime(2026, 9, 9, 0, 5, tzinfo=UTC)  # 09:05 KST
STALE = 600

PORTAL = {
    "processes": [{"name": "engine-swing", "stale_after_minutes": 10}],
    "default_stale_after_minutes": 1560,
}


def _engine(age: float = 30.0, **detail) -> ProcessState:
    return ProcessState(
        process_name="engine-swing",
        status="running",
        last_beat_at=NOW,
        started_at=NOW,
        detail=detail or {"halt_entry": False},
        age_seconds=age,
    )


def _order(**over) -> OrderView:
    base = dict(
        order_id=1,
        stock_id="KRX:005930",
        name="삼성전자",
        side="BUY",
        order_type="MARKET",
        quantity=10,
        price=None,
        status="filled",
        filled_qty=10,
        avg_fill_price=Decimal("71000"),
        error_message=None,
        is_manual=False,
        created_at=NOW,
        updated_at=NOW,
    )
    return OrderView(**{**base, **over})


def _report(**over) -> Report:
    base = dict(
        day=DAY,
        engine=_engine(),
        stale_sec=STALE,
        orders=[_order()],
        positions=[
            PositionView(
                stock_id="KRX:005930",
                name="삼성전자",
                quantity=10,
                avg_price=Decimal("70000"),
                last_close=Decimal("73500"),
                opened_at=NOW,
                synced_at=NOW,
            )
        ],
        pending=[],
        regime=RegimeRow(
            trade_date=DAY,
            regime="safe",
            score=Decimal("0.433"),
            layer_scores=None,
            indicators=None,
            rule_version="2026-09-08b",
            is_override=False,
            override_reason=None,
            kospi_return=None,
            kosdaq_return=None,
        ),
        pnl=None,
        problems=[],
    )
    return Report(**{**base, **over})


def _event(message: str = "잔고 불일치") -> EventRow:
    return EventRow(
        event_id=1,
        process_name="engine-swing",
        level="ERROR",
        category="engine",
        message=message,
        detail=None,
        created_at=NOW,
    )


# --- 서식 ---


def test_주문의_체결_수량을_함께_적는다():
    """**부분체결과 완전체결이 status 로는 안 갈린다** (INTERFACES.md 2.4).

    상태만 적으면 7주 중 3주만 체결된 것을 다 됐다고 읽는다.
    """
    body = render(_report(orders=[_order(quantity=7, filled_qty=3)]))

    assert "BUY 삼성전자 7주 filled (3주 체결 @71,000)" in body


def test_주문이_없으면_없음이라고_적는다():
    """빈 줄만 남기면 '보고가 깨졌나' 와 구분이 안 된다."""
    body = render(_report(orders=[]))

    assert "[ 오늘 주문 0건 ]" in body
    assert "- 없음" in body


def test_거부된_주문의_사유를_싣는다():
    body = render(
        _report(orders=[_order(status="rejected", filled_qty=0, error_message="RC4057")])
    )

    assert "RC4057" in body


def test_보유의_평단_대비_등락을_적는다():
    body = render(_report())

    assert "삼성전자 10주 평단 70,000 종가 73,500 (+5.0%)" in body


def test_국면과_엔진_상태가_맨_위에_온다():
    body = render(_report())
    head = body.split("[")[0]

    assert "running" in head
    assert "safe (+0.433)" in head


def test_진입이_차단되면_드러낸다():
    """가장 조용한 고장이다. 엔진은 살아 있는데 아무것도 사지 않는다."""
    body = render(_report(engine=_engine(halt_entry=True)))

    assert "진입 차단됨" in body


def test_박동이_멎으면_표시한다():
    body = render(_report(engine=_engine(age=STALE + 1)))

    assert "멈춤?" in body


def test_서식이_평문이다():
    """종목명의 기호가 서식과 부딪히면 발송이 400 이 된다."""
    body = render(_report())

    assert "*" not in body
    assert "_" not in body


def test_길면_잘라서라도_보낸다():
    body = render(_report(orders=[_order(name="가" * 500) for _ in range(50)]))

    assert len(body) <= 3800


# --- 경보 등급 ---


def test_평시에는_INFO_다():
    assert level(_report()) == "INFO"


def test_오류가_있으면_WARN_이다():
    assert level(_report(problems=[_event()])) == "WARN"


def test_박동이_멎으면_WARN_이다():
    assert level(_report(engine=_engine(age=STALE + 1))) == "WARN"


def test_기록이_없어도_WARN_이다():
    """heartbeat 행이 없다는 것은 엔진이 한 번도 안 떴다는 뜻이다."""
    assert level(_report(engine=None)) == "WARN"


# --- 임계 시간 ---


def test_화면과_같은_임계값을_쓴다():
    """두 곳이 다르면 화면은 빨간데 보고는 정상이라고 한다."""
    assert stale_seconds(PORTAL, "engine-swing") == 600


def test_설정에_없는_프로세스는_기본값을_쓴다():
    assert stale_seconds(PORTAL, "없는것") == 1560 * 60


def test_실제_설정에서_엔진_임계값을_찾는다():
    from common.config import load_config

    portal = load_config("portal")
    swing = load_config("engine")["swing"]

    assert stale_seconds(portal, swing["process_name"]) < 3600
