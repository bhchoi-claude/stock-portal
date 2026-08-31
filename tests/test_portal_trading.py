# 자동매매 탭과 제어 API. DB 없이 라우팅·확인 토큰·표시를 확인한다

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal

import pytest

from common.db.commands import CommandView
from common.db.filters import FilterRow
from common.db.heartbeat import ProcessState
from common.db.orders import OrderView
from common.db.pnl import PnlRow
from common.db.positions import PositionView
from common.db.signals import SignalView
from portal import control, queries
from portal.app import create_app

UTC_NOW = datetime.fromisoformat("2026-09-01T10:00:00+00:00")

TRADING = {
    "account_id": "paper",
    "process_name": "engine-swing",
    "engine": {
        "status": "running",
        "halt_entry": False,
        "last_beat_at": "2026-09-01T10:00:00+00:00",
        "started_at": "2026-09-01T00:00:00+00:00",
        "age_seconds": 12,
    },
    "pnl": [
        {
            "trade_date": "2026-09-01",
            "deposit": "9718980",
            "eval_amount": "290550",
            "total_asset": "10007949",
            "realized_pnl": None,
            "unrealized_pnl": "10490",
            "trade_count": 2,
        }
    ],
    "positions": [
        {
            "stock_id": "KRX:005930",
            "name": "삼성전자",
            "quantity": 1,
            "avg_price": "251120",
            "last_close": "260000",
            "cost": "251120",
            "value": "260000",
            "pnl": "8880",
            "pnl_rate": "0.035361",
            "opened_at": None,
            "synced_at": "2026-09-01T10:00:00+00:00",
        }
    ],
    "signals": [
        {
            "signal_id": 3,
            "stock_id": "KRX:021050",
            "name": "서원",
            "side": "BUY",
            "strength": "3.5",
            "payload": {"reason": "breakout"},
            "regime_at": "neutral",
            "created_at": "2026-08-31T10:00:00+00:00",
        }
    ],
    "orders": [
        {
            "order_id": 11,
            "stock_id": "KRX:005930",
            "name": "삼성전자",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": 1,
            "price": None,
            "status": "filled",
            "filled_qty": 1,
            "avg_fill_price": "250250",
            "error_message": None,
            "is_manual": False,
            "created_at": "2026-09-01T00:00:00+00:00",
            "updated_at": "2026-09-01T00:01:00+00:00",
        }
    ],
    "commands": [
        {
            "command_id": 4,
            "action": "halt_entry",
            "params": None,
            "status": "done",
            "issued_by": "portal",
            "result": "신규 진입을 차단했습니다",
            "created_at": "2026-09-01T09:00:00+00:00",
            "completed_at": "2026-09-01T09:00:10+00:00",
        }
    ],
    "filters": [
        {
            "filter_id": 2,
            "stock_id": "KRX:021050",
            "name": "서원",
            "strategy": "swing",
            "filter_type": "allow",
            "reason": None,
            "until_date": None,
            "expired": False,
            "created_at": "2026-08-31T10:00:00+00:00",
        }
    ],
}


@pytest.fixture
def client():
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.setattr(queries, "trading", lambda: TRADING)


@pytest.fixture
def issued(monkeypatch):
    """제어 경로가 DB 대신 여기에 쌓이게 한다."""
    seen: dict[str, list] = {"commands": [], "events": [], "filters": [], "removed": []}

    @contextmanager
    def fake_cursor():
        yield None

    def enqueue(cur, *, target, action, params=None, issued_by=None):
        seen["commands"].append(
            {"target": target, "action": action, "params": params, "by": issued_by}
        )
        return 99

    def log_event(cur, process, level, message, *, category=None, detail=None):
        seen["events"].append({"level": level, "message": message, "detail": detail})
        return 1

    def add_filter(cur, **kwargs):
        seen["filters"].append(kwargs)
        return 42

    def remove_filter(cur, filter_id):
        seen["removed"].append(filter_id)
        return filter_id == 42

    monkeypatch.setattr(control, "read_cursor", fake_cursor)
    monkeypatch.setattr(control, "enqueue", enqueue)
    monkeypatch.setattr(control, "log_event", log_event)
    monkeypatch.setattr(control, "add_filter", add_filter)
    monkeypatch.setattr(control, "remove_filter", remove_filter)
    return seen


# --- 화면과 조회 --------------------------------------------------------------


def test_자동매매_탭이_뜬다(client, stub):
    response = client.get("/trading")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "삼성전자" in body
    assert "서원" in body


def test_조회_API_가_JSON_을_준다(client, stub):
    response = client.get("/api/trading")
    assert response.status_code == 200
    assert response.get_json()["account_id"] == "paper"


def test_탭이_모든_화면에_있다(client, stub, monkeypatch):
    monkeypatch.setattr(
        queries,
        "dashboard",
        lambda: {"regime": None, "indicators": [], "processes": [], "trading": {}},
    )
    assert "자동매매" in client.get("/trading").get_data(as_text=True)


def test_전량청산은_접혀_있다(client, stub):
    """되돌릴 수 없는 조작이라 열고 토큰을 쳐야 눌린다 (INTERFACES.md 10.2)."""
    body = client.get("/trading").get_data(as_text=True)
    assert "<details" in body
    assert "LIQUIDATE" in body


def test_allow_필터는_엔진_미반영으로_표시된다(client, stub):
    """화이트리스트 모드가 없다. 듣는 줄 알고 안 듣는 버튼을 만들지 않는다."""
    assert "엔진 미반영" in client.get("/trading").get_data(as_text=True)


def test_스냅샷이_없으면_그렇게_말한다(client, monkeypatch):
    monkeypatch.setattr(queries, "trading", lambda: {**TRADING, "pnl": []})
    assert "15:40" in client.get("/trading").get_data(as_text=True)


# --- 제어 ---------------------------------------------------------------------


def test_진입차단은_토큰_없이_눌린다(client, issued):
    """안전한 쪽으로 가는 조작이라 우발적으로 눌려도 손해가 없다."""
    response = client.post("/api/control/halt-entry")

    assert response.status_code == 200
    assert issued["commands"] == [
        {
            "target": "engine-swing",
            "action": "halt_entry",
            "params": None,
            "by": "portal",
        }
    ]


def test_모든_제어는_event_log_에_남는다(client, issued):
    """INTERFACES.md 10.2."""
    client.post("/api/control/halt-entry")
    assert [e["level"] for e in issued["events"]] == ["WARN"]


def test_전량청산은_확인_토큰이_없으면_거부한다(client, issued):
    response = client.post("/api/control/liquidate-all", data={"confirm": "네"})

    assert response.status_code == 400
    assert "LIQUIDATE" in response.get_json()["error"]
    assert issued["commands"] == []


def test_전량청산은_토큰이_맞으면_들어간다(client, issued):
    response = client.post(
        "/api/control/liquidate-all", data={"confirm": "LIQUIDATE", "reason": "급락"}
    )

    assert response.status_code == 200
    assert issued["commands"][0]["action"] == "liquidate_all"
    assert issued["events"][0]["detail"]["reason"] == "급락"


def test_정지는_프로세스_이름을_확인_토큰으로_받는다(client, issued):
    assert client.post("/api/control/engine/engine-swing/stop").status_code == 400

    response = client.post(
        "/api/control/engine/engine-swing/stop", json={"confirm": "engine-swing"}
    )
    assert response.status_code == 200
    assert issued["commands"][0] == {
        "target": "engine-swing",
        "action": "stop",
        "params": None,
        "by": "portal",
    }


def test_개별_청산은_종목을_params_로_넘긴다(client, issued):
    response = client.post("/api/positions/paper/KRX:005930/close")

    assert response.status_code == 200
    assert issued["commands"][0]["action"] == "close_position"
    assert issued["commands"][0]["params"] == {"stock_id": "KRX:005930"}


def test_모르는_계좌는_404(client, issued):
    assert client.post("/api/positions/swing/KRX:005930/close").status_code == 404
    assert issued["commands"] == []


def test_제외_목록에_넣는다(client, issued):
    response = client.post(
        "/api/filters", data={"stock_id": "KRX:005930", "reason": "악재"}
    )

    assert response.status_code == 200
    assert issued["filters"] == [
        {
            "stock_id": "KRX:005930",
            "strategy": "swing",
            "filter_type": "block",
            "reason": "악재",
            "until_date": None,
        }
    ]


def test_종목_없이는_못_넣는다(client, issued):
    assert client.post("/api/filters", data={"stock_id": "  "}).status_code == 400


def test_모르는_종류는_거부한다(client, issued):
    response = client.post(
        "/api/filters", data={"stock_id": "KRX:005930", "filter_type": "ban"}
    )
    assert response.status_code == 400


def test_기한_형식이_틀리면_400(client, issued):
    response = client.post(
        "/api/filters", data={"stock_id": "KRX:005930", "until_date": "2026-13-99"}
    )
    assert response.status_code == 400
    assert "YYYY-MM-DD" in response.get_json()["error"]


def test_기한을_날짜로_넘긴다(client, issued):
    client.post(
        "/api/filters", data={"stock_id": "KRX:005930", "until_date": "2026-09-30"}
    )
    assert issued["filters"][0]["until_date"] == date(2026, 9, 30)


def test_없는_목록을_지우면_404(client, issued):
    assert client.delete("/api/filters/42").status_code == 200
    assert client.delete("/api/filters/7").status_code == 404


# --- 변환 ---------------------------------------------------------------------


def test_보유_종목의_손익은_마지막_종가로_낸다():
    row = PositionView(
        stock_id="KRX:005930",
        name="삼성전자",
        quantity=2,
        avg_price=Decimal(100),
        last_close=Decimal(120),
        opened_at=None,
        synced_at=UTC_NOW,
    )

    view = queries._position(row)

    assert view["cost"] == "200"
    assert view["value"] == "240"
    assert view["pnl"] == "40"
    assert view["pnl_rate"] == "0.2"


def test_종가가_없으면_손익을_0_이_아니라_비운다():
    """상장 직후처럼 일봉이 없을 수 있다. 0 으로 적으면 본전으로 읽힌다."""
    row = PositionView(
        stock_id="KRX:005930",
        name=None,
        quantity=2,
        avg_price=Decimal(100),
        last_close=None,
        opened_at=None,
        synced_at=UTC_NOW,
    )

    view = queries._position(row)

    assert view["value"] is None
    assert view["pnl"] is None
    assert view["pnl_rate"] is None


def test_진입차단은_heartbeat_detail_에서_읽는다():
    """프로세스의 상태이지 영속 설정이 아니다. 재시작하면 다시 판단한다."""
    state = ProcessState(
        process_name="engine-swing",
        status="running",
        last_beat_at=UTC_NOW,
        started_at=UTC_NOW,
        detail={"halt_entry": True},
        age_seconds=3.0,
    )

    assert queries._engine_state(state)["halt_entry"] is True


def test_엔진이_한_번도_안_돌았으면_상태가_없다():
    assert queries._engine_state(None) == {
        "status": None,
        "halt_entry": None,
        "last_beat_at": None,
    }


def test_지난_기한은_지남으로_표시된다():
    row = FilterRow(
        filter_id=1,
        stock_id="KRX:005930",
        name="삼성전자",
        strategy="swing",
        filter_type="block",
        reason=None,
        until_date=date(2026, 8, 31),
        created_at=UTC_NOW,
    )

    assert queries._filter(row, date(2026, 9, 1))["expired"] is True
    assert queries._filter(row, date(2026, 8, 31))["expired"] is False


def test_기한이_없으면_지나지_않는다():
    row = FilterRow(
        filter_id=1,
        stock_id="KRX:005930",
        name=None,
        strategy="all",
        filter_type="block",
        reason=None,
        until_date=None,
        created_at=UTC_NOW,
    )

    assert queries._filter(row, date(2026, 9, 1))["expired"] is False


def test_손익_스냅샷의_realized_는_비어_있다():
    """원가를 모르므로 채우지 않는다. 0 을 적으면 '오늘 손익 0원' 으로 보인다."""
    row = PnlRow(
        trade_date=date(2026, 9, 1),
        deposit=Decimal(100),
        eval_amount=Decimal(50),
        total_asset=Decimal(150),
        realized_pnl=None,
        unrealized_pnl=Decimal(5),
        trade_count=1,
    )

    assert queries._pnl(row)["realized_pnl"] is None


def test_계획에는_수량이_없다():
    row = SignalView(
        signal_id=1,
        stock_id="KRX:005930",
        name="삼성전자",
        side="BUY",
        strength=Decimal("3.5"),
        payload={"reason": "breakout"},
        regime_at="neutral",
        created_at=UTC_NOW,
    )

    assert "quantity" not in queries._signal(row)


def test_주문은_체결량과_체결가를_따로_준다():
    row = OrderView(
        order_id=1,
        stock_id="KRX:005930",
        name="삼성전자",
        side="BUY",
        order_type="MARKET",
        quantity=10,
        price=None,
        status="partial",
        filled_qty=4,
        avg_fill_price=Decimal(250250),
        error_message=None,
        is_manual=False,
        created_at=UTC_NOW,
        updated_at=UTC_NOW,
    )

    view = queries._order(row)

    assert (view["quantity"], view["filled_qty"]) == (10, 4)
    assert view["avg_fill_price"] == "250250"


def test_명령_결과를_그대로_보여준다():
    row = CommandView(
        command_id=1,
        target="engine-swing",
        action="liquidate_all",
        params=None,
        status="done",
        issued_by="portal",
        result="청산 주문 3건",
        created_at=UTC_NOW,
        completed_at=UTC_NOW,
    )

    assert queries._command(row)["result"] == "청산 주문 3건"
