# 포털 조회 API 와 화면. DB 없이 라우팅·변환·상태 판정을 확인한다

from decimal import Decimal

import pytest

from common.db.heartbeat import ProcessState
from portal import queries
from portal.app import create_app, duration, kst, num, percent, ratio_pct


@pytest.fixture
def client():
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


REGIME = {
    "trade_date": "2026-08-28",
    "regime": "neutral",
    "score": "0.120000",
    "layer_scores": {"risk": "0.1"},
    "indicators": {"VKOSPI": "15.2"},
    "rule_version": "2026-08-29",
    "is_override": False,
    "override_reason": None,
    "kospi_return": "0.0123",
    "kosdaq_return": None,
}

INDICATOR = {
    "indicator_code": "VKOSPI",
    "name": "변동성지수",
    "layer": "risk",
    "frequency": "daily",
    "unit": "pt",
    "use_in_regime": True,
    "period_date": "2026-08-28",
    "value": "15.200000",
    "change_rate": "-0.0250",
}

PROCESS = {
    "name": "daily",
    "label": "일봉·조정 갱신",
    "state": "ok",
    "status": "idle",
    "last_beat_at": "2026-08-29T10:00:00+00:00",
    "started_at": "2026-08-29T09:58:00+00:00",
    "age_seconds": 120,
    "stale_after_minutes": 26 * 60,
    "detail": {"exit_code": 0},
}

EVENT = {
    "event_id": 1,
    "process_name": "daily",
    "level": "ERROR",
    "category": "collect",
    "message": "일봉 적재 실패",
    "detail": None,
    "created_at": "2026-08-29T10:00:00+00:00",
}


KEYWORD = {
    "keyword_id": 7,
    "term": "유리기판",
    "mention_count": 30,
    "weighted_count": "30.00",
    "ma7": "4.300000",
    "surge_ratio": "7.000000",
    "is_new": False,
    "is_surging": True,
    "is_confirmed": False,
}

DISCLOSURE = {
    "rcept_no": "20260828000123",
    "stock_id": "KRX:005930",
    "corp_name": "삼성전자",
    "report_name": "주요사항보고서",
    "disclosure_type": "B",
    "submitted_at": "2026-08-27T15:00:00+00:00",
    "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260828000123",
}

MESSAGE = {
    "message_id": 1,
    "source": "가치투자클럽",
    "content": "유리기판 수주 확대",
    "published_at": "2026-08-29T10:00:00+00:00",
}


@pytest.fixture
def stub(monkeypatch):
    """조회 함수를 고정값으로 바꾼다. 라우팅과 변환만 본다."""
    monkeypatch.setattr(
        queries,
        "dashboard",
        lambda: {
            "regime": REGIME,
            "indicators": [INDICATOR],
            "processes": [PROCESS],
            "trading": {"accounts": [], "positions": [], "executions": []},
        },
    )
    monkeypatch.setattr(queries, "regime_now", lambda: REGIME)
    monkeypatch.setattr(
        queries,
        "regime_range",
        lambda start, end: {"from": "2026-06-01", "to": "2026-08-29", "rows": [REGIME]},
    )
    monkeypatch.setattr(queries, "indicators", lambda: [INDICATOR])
    monkeypatch.setattr(queries, "processes", lambda: [PROCESS])
    monkeypatch.setattr(queries, "events", lambda levels, limit: [EVENT])
    monkeypatch.setattr(
        queries,
        "keywords_surge",
        lambda day: {"date": "2026-08-30", "rows": [KEYWORD]},
    )
    monkeypatch.setattr(
        queries,
        "messages",
        lambda term, since, limit: {
            "keyword": term,
            "from": "2026-08-23",
            "rows": [MESSAGE],
        },
    )
    monkeypatch.setattr(queries, "disclosures", lambda limit: [DISCLOSURE])
    monkeypatch.setattr(
        queries,
        "channels",
        lambda: [
            {
                "name": "가치투자클럽",
                "messages": 194,
                "last_published_at": "2026-08-29T10:00:00+00:00",
            }
        ],
    )


@pytest.mark.parametrize(
    "path",
    [
        "/api/dashboard",
        "/api/regime/current",
        "/api/regime/history",
        "/api/indicators",
        "/api/processes",
        "/api/events",
        "/api/keywords/surge",
        "/api/disclosures",
        "/api/messages?keyword=유리기판",
    ],
)
def test_api_ok(client, stub, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.is_json


def test_dashboard_keeps_trading_empty(client, stub):
    """매매 항목은 비어 있는 것이 정상이다 (Phase 8 까지)."""
    trading = client.get("/api/dashboard").get_json()["trading"]
    assert trading == {"accounts": [], "positions": [], "executions": []}


def test_bad_date_is_400(client, stub):
    response = client.get("/api/regime/history?from=2026-13-99")
    assert response.status_code == 400
    assert "YYYY-MM-DD" in response.get_json()["error"]


def test_unknown_level_is_400(client, stub):
    response = client.get("/api/events?level=SEVERE")
    assert response.status_code == 400


def test_level_is_split_and_uppercased(client, monkeypatch):
    seen = {}

    def fake(levels, limit):
        seen["levels"] = levels
        seen["limit"] = limit
        return []

    monkeypatch.setattr(queries, "events", fake)
    assert client.get("/api/events?level=error,critical&limit=10").status_code == 200
    assert seen == {"levels": ["ERROR", "CRITICAL"], "limit": 10}


@pytest.mark.parametrize(
    "path", ["/", "/market", "/ops", "/news", "/news?keyword=유리기판"]
)
def test_pages_render(client, stub, path):
    response = client.get(path)
    assert response.status_code == 200
    assert "증권 포털" in response.get_data(as_text=True)


def test_process_state_from_heartbeat():
    """heartbeat 의 status 와 화면이 쓰는 state 는 다르다."""
    assert _state("idle", age_hours=1) == "ok"
    assert _state("error", age_hours=1) == "error"
    # 신호가 끊긴 running 은 죽은 것이다. 배치가 죽으면 error 를 남길 틈이 없다
    assert _state("running", age_hours=48) == "stale"
    assert _state("idle", age_hours=48) == "stale"
    assert queries._process("x", "x", None, 26 * 60)["state"] == "unknown"


def _state(status: str, age_hours: float) -> str:
    state = ProcessState("x", status, None, None, None, age_hours * 3600)
    return queries._process("x", "x", state, 26 * 60)["state"]


def test_filters():
    assert num("15.200000") == "15.2"
    assert num("1234567.000000") == "1,234,567"
    assert num(None) == "-"
    assert kst("2026-08-29T10:00:00+00:00") == "08-29 19:00"
    assert kst(None) == "-"


def test_duration_reads_as_hours_or_minutes():
    assert duration(1560) == "26시간"
    assert duration(10) == "10분"
    assert duration(None) == "-"


def test_percent_and_ratio_are_different_units():
    """change_rate 는 비율, kospi_return 은 이미 퍼센트다. 섞으면 100배 틀린다."""
    assert ratio_pct("-0.0250") == "-2.50%"
    assert percent("-1.7865") == "-1.79%"
    assert ratio_pct(None) == "-"
    assert percent(None) == "-"


def test_num_keeps_decimal_exact():
    """float 로 바꾸면 값이 미세하게 달라진다. Decimal 로만 다룬다."""
    assert num(str(Decimal("0.1") + Decimal("0.2"))) == "0.3"


def test_messages_needs_a_keyword(client, stub):
    assert client.get("/api/messages").status_code == 400


def test_merge_reads_form_and_json(client, monkeypatch):
    """화면은 폼으로, API 는 JSON 으로 보낸다. 같은 엔드포인트다."""
    seen = []
    monkeypatch.setattr(
        queries, "merge", lambda into, ids: seen.append((into, ids)) or 1
    )

    assert (
        client.post(
            "/api/keywords/merge", data={"into": "7", "from": ["8", "9"]}
        ).status_code
        == 200
    )
    assert (
        client.post("/api/keywords/merge", json={"into": 7, "from": [8]}).status_code
        == 200
    )
    assert seen == [(7, [8, 9]), (7, [8])]


def test_merge_rejects_empty(client, stub):
    assert (
        client.post("/api/keywords/merge", json={"into": 7, "from": []}).status_code
        == 400
    )
    assert client.post("/api/keywords/merge", json={"from": [8]}).status_code == 400
