# 예약 시험주문 도구. 주문이 두 번 나가지 않는지가 이 파일의 요점이다

import json
import pathlib
from decimal import Decimal

import pytest

from common.broker import testorder
from common.broker.errors import PermanentError, TransientError


class FakeBroker:
    """`_call_once` 만 흉내낸다. 도구가 그것 하나로 모든 호출을 한다."""

    def __init__(self, quotes=None, order_results=None) -> None:
        self.quotes = list(quotes or [])
        self.order_results = list(order_results or [])
        self.calls: list[tuple[str, dict]] = []

    def _call_once(self, api_id, path, body, **kwargs):
        self.calls.append((api_id, body))

        if api_id == "ka10001":
            return self.quotes.pop(0)
        if api_id == "kt10000":
            result = self.order_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return {"return_code": 0, "api": api_id}


def quote(base: str, low: str = "-182000") -> dict:
    return {
        "base_pric": base,
        "lst_pric": low,
        "upl_pric": "+338000",
        "cur_prc": "+260000",
    }


@pytest.fixture
def args():
    return testorder._parse(
        ["--price-attempts", "3", "--price-wait-sec", "0", "--order-wait-sec", "0"]
    )


@pytest.fixture
def no_db(monkeypatch):
    """`price_daily` 대신 전 거래일 종가를 직접 준다."""

    def fake(value):
        monkeypatch.setattr(testorder, "_last_close", lambda: value)

    return fake


# --- 하한가 -------------------------------------------------------------------


def test_기준가가_맞으면_부호를_뗀_정수를_준다(args, no_db):
    """`ord_uv` 는 정수만 받는다. `-182000` 을 그대로 보내면 1517 로 거부된다."""
    no_db(Decimal(260000))
    broker = FakeBroker(quotes=[quote("260000")])

    price = testorder._wait_for_price(broker, {"steps": []}, args)

    assert price == "182000"


def test_기준가가_안_넘어갔으면_기다렸다_다시_본다(args, no_db):
    """장 전에 읽으면 지난 장의 하한가가 나온다 (2026-08-31 실측)."""
    no_db(Decimal(260000))
    broker = FakeBroker(quotes=[quote("257000", "-180000"), quote("260000")])

    price = testorder._wait_for_price(broker, {"steps": []}, args)

    assert price == "182000"
    assert len(broker.calls) == 2


def test_끝까지_안_넘어가면_주문하지_않는다(args, no_db):
    """옛 하한가로 내면 범위를 벗어나 거부되고 아무것도 못 잰다."""
    no_db(Decimal(260000))
    broker = FakeBroker(quotes=[quote("257000", "-180000")] * 3)

    assert testorder._wait_for_price(broker, {"steps": []}, args) is None


def test_일봉이_없으면_대조를_건너뛴다(args, no_db):
    """대조는 보조 수단이다. 없다고 멈추면 그날을 통째로 잃는다."""
    no_db(None)
    broker = FakeBroker(quotes=[quote("257000", "-180000")])

    assert testorder._wait_for_price(broker, {"steps": []}, args) == "180000"


# --- 주문 ---------------------------------------------------------------------


def test_응답을_못_받으면_다시_걸지_않는다(args):
    """**이 파일에서 가장 중요한 테스트다.**

    접수됐는지 모르는 상태에서 다시 걸면 그대로 중복 주문이다 (CLAUDE.md 3).
    """
    broker = FakeBroker(order_results=[TransientError("타임아웃")])
    record = {"steps": []}

    assert testorder._place(broker, record, "182000", args) is None
    assert len(broker.calls) == 1
    assert "접수 여부" in record["steps"][0]["note"]


def test_거부는_최종_상태라_다시_낸다(args):
    """장이 아직 안 열렸을 때가 이 경로다. 거부는 접수 실패가 확정된 것이다."""
    broker = FakeBroker(
        order_results=[
            {"return_code": 20, "return_msg": "[2000](RC4058:모의투자 장종료)"},
            {"return_code": 0, "ord_no": "0060503"},
        ]
    )

    assert testorder._place(broker, {"steps": []}, "182000", args) == "0060503"
    assert len(broker.calls) == 2


def test_거부가_이어지면_횟수를_넘기지_않는다(args):
    broker = FakeBroker(order_results=[{"return_code": 20, "return_msg": "장종료"}] * 3)

    assert testorder._place(broker, {"steps": []}, "182000", args) is None
    assert len(broker.calls) == args.order_attempts


def test_영구_오류는_바로_멈춘다(args):
    broker = FakeBroker(order_results=[PermanentError("앱키 불일치")])

    assert testorder._place(broker, {"steps": []}, "182000", args) is None
    assert len(broker.calls) == 1


def test_주문번호_필드가_없어도_기록은_남긴다(args):
    """`ord_no` 는 아직 실측 못 한 가정이다. 틀렸을 때 키 목록이 남아야 한다."""
    broker = FakeBroker(order_results=[{"return_code": 0, "odno": "0060503"}])
    record = {"steps": []}

    assert testorder._place(broker, record, "182000", args) is None
    assert record["steps"][0]["keys"] == ["odno", "return_code"]


# --- 기록과 재실행 -------------------------------------------------------------


def test_한_단계가_실패해도_다음으로_넘어간다():
    """취소까지 가는 것이 중요하다. 중간 조회 실패로 멈추면 주문이 남는다."""
    record = {"steps": []}

    def boom():
        raise RuntimeError("조회 실패")

    testorder._step(record, "미체결", boom)

    assert "RuntimeError" in record["steps"][0]["error"]


def test_이미_돌았으면_다시_주문하지_않는다(tmp_path, monkeypatch):
    """타이머가 두 번 발화해도 주문은 한 번만 나간다."""
    out = tmp_path / "testorder.json"
    out.with_suffix(".done").write_text("done", encoding="utf-8")

    def never(*a, **k):
        raise AssertionError("브로커를 만들면 안 된다")

    monkeypatch.setattr(testorder, "KiwoomBroker", never)

    assert testorder.main(["--out", str(out)]) == 0


def test_중단돼도_기록을_남긴다(tmp_path, monkeypatch):
    out = tmp_path / "testorder.json"
    monkeypatch.setattr(testorder, "KiwoomBroker", lambda **k: FakeBroker())
    monkeypatch.setattr(
        testorder, "_run", lambda *a: (_ for _ in ()).throw(RuntimeError("끊김"))
    )

    assert testorder.main(["--out", str(out)]) == 1

    record = json.loads(out.read_text(encoding="utf-8"))
    assert "끊김" in record["fatal"]
    assert pathlib.Path(str(out.with_suffix(".done"))).exists()
