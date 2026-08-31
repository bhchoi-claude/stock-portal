# 장중 실측 도구. 주문이 두 번 나가지 않는지가 이 파일의 요점이다

import json
from datetime import datetime
from decimal import Decimal

import pytest

from common.broker import testorder
from common.broker.errors import PermanentError, TransientError

SEOUL = testorder.SEOUL


class FakeBroker:
    """`_call_once` 만 흉내낸다. 도구가 그것 하나로 모든 호출을 한다."""

    def __init__(self, quotes=None, orders=None, lists=None) -> None:
        self.quotes = list(quotes or [])
        self.orders = list(orders or [])
        self.lists = lists or {}
        self.calls: list[tuple[str, dict]] = []

    def _call_once(self, api_id, path, body, **kwargs):
        self.calls.append((api_id, body))
        if api_id == "ka10001":
            return self.quotes.pop(0)
        if api_id in ("kt10000", "kt10001", "kt10003"):
            result = self.orders.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return self.lists.get(api_id, {"return_code": 0})


def quote(base="260000", low="-182000", cur="+260000", open_pric="+261000"):
    return {
        "base_pric": base,
        "lst_pric": low,
        "upl_pric": "+338000",
        "cur_prc": cur,
        "open_pric": open_pric,
    }


@pytest.fixture
def args():
    return testorder._parse(["--price-attempts", "3", "--price-wait-sec", "0"])


@pytest.fixture
def close(monkeypatch):
    def apply(value):
        monkeypatch.setattr(testorder, "_last_close", lambda: value)

    return apply


# --- 주문: 재시도 금지가 핵심이다 ---------------------------------------------


def test_응답을_못_받으면_다시_걸지_않는다():
    """**이 파일에서 가장 중요한 테스트다.**

    접수됐는지 모르는 상태에서 다시 걸면 그대로 중복 주문이다 (CLAUDE.md 3).
    """
    broker = FakeBroker(orders=[TransientError("타임아웃")])
    state: dict = {}

    assert testorder._order(broker, state, "kt10000", {"a": "1"}) is None
    assert len(broker.calls) == 1
    assert state["unknown_orders"] == [{"a": "1"}]


def test_영구_오류도_다시_걸지_않는다():
    broker = FakeBroker(orders=[PermanentError("앱키 불일치")])

    assert testorder._order(broker, {}, "kt10000", {}) is None
    assert len(broker.calls) == 1


def test_거부는_사유를_남기고_넘어간다():
    broker = FakeBroker(orders=[{"return_code": 20, "return_msg": "장종료"}])
    state: dict = {}

    assert testorder._order(broker, state, "kt10000", {}) is None
    assert state["rejected"] == ["장종료"]


def test_접수되면_주문번호를_준다():
    broker = FakeBroker(orders=[{"return_code": 0, "ord_no": "0060503"}])

    assert testorder._order(broker, {}, "kt10000", {}) == "0060503"


def test_주문번호_필드가_다르면_키_목록을_남긴다():
    """`ord_no` 는 아직 실측 못 한 가정이다. 틀렸을 때 단서가 남아야 한다."""
    broker = FakeBroker(orders=[{"return_code": 0, "odno": "0060503"}])
    state: dict = {}

    assert testorder._order(broker, state, "kt10000", {}) is None
    assert state["success_keys"] == ["odno", "return_code"]


# --- 하한가 -------------------------------------------------------------------


def test_기준가가_맞으면_부호를_뗀_정수를_준다(args, close):
    """`ord_uv` 는 정수만 받는다. `-182000` 을 그대로 보내면 1517 로 거부된다."""
    close(Decimal(260000))
    broker = FakeBroker(quotes=[quote()])

    assert testorder._limit_price(broker, args) == "182000"


def test_기준가가_안_넘어갔으면_기다렸다_다시_본다(args, close):
    """장 전에 읽으면 지난 장의 하한가가 나온다 (2026-08-31 실측)."""
    close(Decimal(260000))
    broker = FakeBroker(quotes=[quote(base="257000", low="-180000"), quote()])

    assert testorder._limit_price(broker, args) == "182000"
    assert len(broker.calls) == 2


def test_끝까지_안_넘어가면_가격을_주지_않는다(args, close):
    """옛 하한가로 내면 범위를 벗어나 거부되고 아무것도 못 잰다."""
    close(Decimal(260000))
    broker = FakeBroker(quotes=[quote(base="257000")] * 3)

    assert testorder._limit_price(broker, args) is None


def test_일봉을_못_읽으면_대조를_건너뛴다(args, close):
    """대조는 보조 수단이다. 없다고 멈추면 그날을 통째로 잃는다."""
    close(None)
    broker = FakeBroker(quotes=[quote(base="257000", low="-180000")])

    assert testorder._limit_price(broker, args) == "180000"


# --- 부분체결: 여러 행을 놓치지 않는다 -----------------------------------------


def test_같은_주문번호의_행을_전부_뽑는다():
    """한 주문이 여러 행으로 오면 수량 합산이 틀린다. 하나만 찾으면 못 본다."""
    data = {
        "cntr": [
            {"ord_no": "0060503", "cntr_qty": "40"},
            {"ord_no": "0060503", "cntr_qty": "60"},
            {"ord_no": "0060504", "cntr_qty": "10"},
        ]
    }

    rows = testorder._rows(data, "0060503")

    assert [row["cntr_qty"] for row in rows] == ["40", "60"]


def test_주문번호가_없으면_빈_목록이다():
    assert testorder._rows({"cntr": [{"ord_no": "1"}]}, None) == []


# --- 슬리피지 -----------------------------------------------------------------


def test_체결가와_시가의_차이를_비율로_낸다(args):
    """백테스트가 다음 날 시가 체결을 가정한다. 그 가정이 얼마나 맞는지가 값이다."""
    broker = FakeBroker(
        quotes=[quote(open_pric="+260000")],
        lists={"ka10076": {"cntr": [{"ord_no": "0060503", "cntr_pric": "+260520"}]}},
    )

    result = testorder._slippage(broker, {"market_buy": "0060503"}, args)

    assert result["open_pric"] == "260000"
    assert result["fill_price"] == "260520"
    assert Decimal(result["slippage"]) == Decimal(520) / Decimal(260000)


def test_체결이_없으면_슬리피지를_내지_않는다(args):
    broker = FakeBroker(quotes=[quote()], lists={"ka10076": {"cntr": []}})

    result = testorder._slippage(broker, {"market_buy": "0060503"}, args)

    assert result["filled_row"] is None
    assert "slippage" not in result


# --- 시각표 -------------------------------------------------------------------


@pytest.fixture
def at_0825(monkeypatch):
    """시계를 08:25 로 고정한다. 실제 시각에 기대면 자정 근처에서 깨진다."""

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 1, 8, 25, tzinfo=SEOUL)

    monkeypatch.setattr(testorder, "datetime", Frozen)
    slept: list[float] = []
    monkeypatch.setattr(testorder.time, "sleep", slept.append)
    return slept


def test_시각이_지났으면_기다리지_않는다(args, at_0825):
    """늦게 시작해도 남은 단계는 돌아야 한다."""
    testorder._sleep_until("08:00", "지난 단계", args)

    assert at_0825 == []


def test_시각까지_기다린다(args, at_0825):
    testorder._sleep_until("08:28", "동시호가", args)

    assert at_0825 == [180]


def test_한_번에_너무_오래_자지_않는다(args, at_0825):
    """시계가 어긋났을 때 하루를 통째로 잃지 않게 상한을 둔다."""
    testorder._sleep_until("16:12", "먼 단계", args)

    assert at_0825 == [args.max_wait_sec]


def test_시각표가_시간_순이다(args):
    times = [at for at, _, _ in testorder._plan(args)]
    assert times == sorted(times)


# --- 기록 ---------------------------------------------------------------------


def test_한_단계가_실패해도_그때까지를_저장한다(tmp_path):
    """중간에 죽어도 실측이 남아야 한다. 끝에 한 번만 저장하면 다 사라진다."""
    out = tmp_path / "run.json"
    record: dict = {"steps": []}

    def boom():
        raise RuntimeError("조회 실패")

    testorder._step(record, out, "미체결", boom)

    saved = json.loads(out.read_text(encoding="utf-8"))
    assert "RuntimeError" in saved["steps"][0]["error"]


def test_이미_돌았으면_다시_주문하지_않는다(tmp_path, monkeypatch):
    """타이머가 두 번 발화해도 주문은 한 번만 나간다."""
    out = tmp_path / "run.json"
    out.with_suffix(".done").write_text("done", encoding="utf-8")

    def never(*a, **k):
        raise AssertionError("브로커를 만들면 안 된다")

    monkeypatch.setattr(testorder, "KiwoomBroker", never)

    assert testorder.main(["--out", str(out)]) == 0
