# 키움 응답 파싱을 실제 응답 샘플로 고정해 확인한다

import json
import logging
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from common.broker.base import OrderRequest
from common.broker.errors import PermanentError, RateLimitError, TransientError
from common.broker.kiwoom import (
    KiwoomBroker,
    strip_code_prefix,
    strip_sign,
    to_amount,
    to_utc,
)
from common.types import OrderType, Side

# 2026-08-28 모의투자 계좌 ka10080 응답에서 그대로 옮긴 행이다
MINUTE_ROW = {
    "cur_prc": "-257000",
    "trde_qty": "1147111",
    "cntr_tm": "20260828153000",
    "open_pric": "-257000",
    "high_pric": "-257000",
    "low_pric": "-257000",
    "acc_trde_qty": "14698803",
    "pred_pre": "-9000",
    "pred_pre_sig": "5",
}


def test_가격의_전일대비_부호를_벗긴다():
    # -257000 은 음수 가격이 아니라 하락 표시다
    assert strip_sign("-257000") == Decimal(257000)
    assert strip_sign("+257000") == Decimal(257000)
    assert strip_sign("257000") == Decimal(257000)


def test_체결시각을_kst_로_읽어_utc_로_바꾼다():
    # 15:30 KST 는 06:30 UTC 다
    assert to_utc("20260828153000") == datetime(2026, 8, 28, 6, 30, tzinfo=UTC)


def test_분봉_한_행_매핑():
    candle = KiwoomBroker._to_candle("KRX:005930", MINUTE_ROW)

    assert candle.stock_id == "KRX:005930"
    assert candle.ts == datetime(2026, 8, 28, 6, 30, tzinfo=UTC)
    assert candle.open == Decimal(257000)
    assert candle.high == Decimal(257000)
    assert candle.low == Decimal(257000)
    assert candle.close == Decimal(257000)
    assert candle.volume == 1147111


def test_음수_가격이_저장되지_않는다():
    candle = KiwoomBroker._to_candle("KRX:005930", MINUTE_ROW)

    assert candle.open > 0
    assert candle.low > 0


def test_누적거래량이_아니라_봉_거래량을_쓴다():
    # acc_trde_qty 는 14,698,803 이고 trde_qty 는 1,147,111 이다
    assert KiwoomBroker._to_candle("KRX:005930", MINUTE_ROW).volume == 1147111


def test_종목코드에서_접두어를_뗀다():
    # 키움은 접두어를 붙이면 return_code = 5 로 거부한다
    assert KiwoomBroker.to_code("KRX:005930") == "005930"
    assert KiwoomBroker.to_code("KRX:00104K") == "00104K"


@pytest.mark.parametrize(
    "code, expected",
    [
        (429, RateLimitError),
        (401, PermanentError),
        (403, PermanentError),
        (500, TransientError),
        (503, TransientError),
        (400, PermanentError),
    ],
)
def test_http_상태를_에러로_분류한다(code, expected):
    class FakeError:
        pass

    exc = FakeError()
    exc.code = code

    assert isinstance(KiwoomBroker._http_error(exc), expected)


def test_401_은_재시도_대상이_아니다():
    class FakeError:
        pass

    exc = FakeError()
    exc.code = 401

    assert not isinstance(KiwoomBroker._http_error(exc), TransientError)


# 2026-08-30 모의투자 계좌 kt00001 응답에서 그대로 옮겼다. 쓰는 필드만 남겼다
DEPOSIT_RESPONSE = {
    "entr": "000000010000000",
    "profa_ch": "000000000000000",
    "ord_alow_amt": "000000010000000",
    "pymn_alow_amt": "000000010000000",
    "20stk_ord_alow_amt": "000000050000000",
    "30stk_ord_alow_amt": "000000033333333",
    "40stk_ord_alow_amt": "000000025000000",
    "50stk_ord_alow_amt": "000000020000000",
    "60stk_ord_alow_amt": "000000016666667",
    "100stk_ord_alow_amt": "000000010000000",
    "d1_entra": "000000010000000",
    "d2_entra": "000000010000000",
    "stk_entr_prst": [],
    "return_code": 0,
    "return_msg": "모의투자 조회완료",
}

# 2026-08-30 모의투자 계좌 kt00018 응답. 보유종목이 없는 상태다
BALANCE_RESPONSE = {
    "tot_pur_amt": "000000000000000",
    "tot_evlt_amt": "000000000000000",
    "tot_evlt_pl": "000000000000000",
    "tot_prft_rt": "000000000.00",
    "prsm_dpst_aset_amt": "000000010000000",
    "tot_loan_amt": "000000000000000",
    "acnt_evlt_remn_indv_tot": [],
    "return_code": 0,
    "return_msg": "모의투자 해당조회내역이 없습니다.",
}

# 2026-08-31 모의투자 계좌 kt00018 의 보유종목 행. 그대로 옮겼다
HOLDING_ROW = {
    "stk_cd": "A005930",
    "stk_nm": "삼성전자",
    "evltv_prft": "000000000007450",
    "prft_rt": "000000002.98",
    "pur_pric": "000000000250250",
    "pred_close_pric": "000000257000",
    "rmnd_qty": "000000000000001",
    "trde_able_qty": "000000000000001",
    "cur_prc": "000000260000",
    "pur_amt": "000000000250250",
    "pur_cmsn": "000000000000870",
    "evlt_amt": "000000000260000",
    "sell_cmsn": "000000000000910",
    "tax": "000000000000520",
    "sum_cmsn": "000000000001780",
    "poss_rt": "000000089.36",
    "crd_tp": "00",
}


def test_금액은_제로패딩_문자열이다():
    """000000010000000 이 1,000만원이다 (2026-08-30 실측)."""
    assert to_amount("000000010000000") == Decimal(10_000_000)
    assert to_amount("000000000000000") == Decimal(0)
    assert to_amount("") == Decimal(0)
    assert to_amount(None) == Decimal(0)


def test_잔고는_두_응답을_합친다(monkeypatch):
    """kt00001 에 평가금액이 없고 kt00018 에 주문가능금액이 없다."""
    broker = _stub_broker(monkeypatch)
    balance = broker.get_balance("paper")

    assert balance.account_id == "paper"
    assert balance.deposit == Decimal(10_000_000)
    assert balance.eval_amount == Decimal(0)
    assert balance.total_asset == Decimal(10_000_000)


def test_주문가능금액은_증거금_100퍼센트_기준이다(monkeypatch):
    """**미수를 쓰지 않는다.** 20% 기준 5,000만원을 쓰면 실계좌에서 미수가 난다."""
    broker = _stub_broker(monkeypatch)
    balance = broker.get_balance("paper")

    assert balance.available == Decimal(10_000_000)
    assert balance.available != to_amount(DEPOSIT_RESPONSE["20stk_ord_alow_amt"])


def test_잔고_조회에_계좌번호를_보내지_않는다(monkeypatch):
    """계좌는 앱키·토큰에 묶인다 (2026-08-30 실측)."""
    sent = []
    broker = _stub_broker(monkeypatch, sent)
    broker.get_balance("paper")

    assert len(sent) == 2
    assert all("acnt" not in json.dumps(body) for _, body in sent)
    assert sent[0][0] == "kt00001"
    assert sent[1][0] == "kt00018"


def _stub_broker(monkeypatch, sent: list | None = None) -> KiwoomBroker:
    """네트워크 없이 응답만 갈아끼운다."""
    broker = KiwoomBroker.__new__(KiwoomBroker)
    responses = {"kt00001": DEPOSIT_RESPONSE, "kt00018": BALANCE_RESPONSE}

    def fake_call(api_id, path, body, **extra):
        if sent is not None:
            sent.append((api_id, body))
        return responses[api_id]

    monkeypatch.setattr(broker, "_call", fake_call)
    return broker


def test_잔고_종목코드의_접두어를_벗긴다():
    """kt00018 만 A005930 처럼 접두어를 붙인다 (2026-08-31 실측).

    벗기지 않으면 KRX:A005930 이 되어 DB 의 어느 종목과도 안 맞는다.
    """
    assert strip_code_prefix("A005930") == "005930"
    assert strip_code_prefix("005930") == "005930"


def test_보유종목_한_행_매핑():
    position = KiwoomBroker._to_position("paper", HOLDING_ROW)

    assert position.stock_id == "KRX:005930"
    assert position.quantity == 1


def test_평단가에_매입수수료를_포함한다():
    """백테스트 Portfolio 와 같은 정의다. 현금이 준 만큼이 원가다.

    맞추지 않으면 같은 전략이 백테스트와 실전에서 다른 손절가를 본다.
    """
    position = KiwoomBroker._to_position("paper", HOLDING_ROW)

    # 매입금액 250,250 + 매입수수료 870
    assert position.avg_price == Decimal(251_120)
    assert position.avg_price > to_amount(HOLDING_ROW["pur_pric"])


def test_수량이_0_인_행은_포지션이_아니다(monkeypatch):
    empty = {**HOLDING_ROW, "rmnd_qty": "000000000000000"}
    broker = _stub_broker(monkeypatch)
    monkeypatch.setattr(
        broker,
        "_call",
        lambda *a, **k: {**BALANCE_RESPONSE, "acnt_evlt_remn_indv_tot": [empty]},
    )

    assert broker.get_positions("paper") == []


def test_보유종목이_없으면_빈_목록이다(monkeypatch):
    broker = _stub_broker(monkeypatch)
    assert broker.get_positions("paper") == []


def test_보유종목_목록을_읽는다(monkeypatch):
    broker = _stub_broker(monkeypatch)
    monkeypatch.setattr(
        broker,
        "_call",
        lambda *a, **k: {**BALANCE_RESPONSE, "acnt_evlt_remn_indv_tot": [HOLDING_ROW]},
    )
    positions = broker.get_positions("paper")

    assert len(positions) == 1
    assert positions[0].account_id == "paper"
    assert positions[0].currency == "KRW"


# ---- 주문용 단발 호출 경로 ----


def _counting_broker(monkeypatch) -> tuple[KiwoomBroker, list]:
    """네트워크 없이 호출 횟수만 센다. TransientError 를 계속 던진다."""
    broker = KiwoomBroker.__new__(KiwoomBroker)
    broker._min_interval = 0.0
    broker._max_attempts = 3
    broker._last_call = {}
    broker._lock = threading.Lock()
    calls = []

    def fake_request(path, headers, body, **kwargs):
        calls.append(path)
        raise TransientError("타임아웃")

    monkeypatch.setattr(broker, "_auth_header", dict)
    monkeypatch.setattr(broker, "_request", fake_request)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    return broker, calls


def test_조회는_transient_를_재시도한다(monkeypatch):
    broker, calls = _counting_broker(monkeypatch)

    with pytest.raises(TransientError):
        broker._call("kt00001", "/api/dostk/acnt", {})

    assert len(calls) == 3


def test_주문_경로는_재시도하지_않는다(monkeypatch):
    """응답을 못 받았을 때 다시 걸면 그대로 중복 주문이다 (CLAUDE.md 3).

    접수 여부는 `get_order_status` 로 확인한다.
    """
    broker, calls = _counting_broker(monkeypatch)

    with pytest.raises(TransientError):
        broker._call_once("kt10000", "/api/dostk/ordr", {})

    assert len(calls) == 1


# ---- 주문 접수 ----

# 2026-08-31 모의투자 kt10000 거부 응답이다. 장 외 시간에 그대로 받았다
REJECT_RESPONSE = {
    "return_code": 20,
    "return_msg": "[2000](RC4058:모의투자 장종료)",
}


def _order_broker(monkeypatch, response: dict, sent: list) -> KiwoomBroker:
    """주문 호출만 가로챈다. 재시도 경로를 쓰면 즉시 터지게 해둔다."""
    broker = KiwoomBroker.__new__(KiwoomBroker)

    def fake_call_once(api_id, path, body, **kwargs):
        sent.append((api_id, path, body, kwargs))
        return response

    def forbidden(*args, **kwargs):
        raise AssertionError("주문이 재시도 경로(_call)를 탔다")

    monkeypatch.setattr(broker, "_call_once", fake_call_once)
    monkeypatch.setattr(broker, "_call", forbidden)
    return broker


def _request(**overrides) -> OrderRequest:
    base = {
        "client_order_id": "01JABCDEF0000000000000000",
        "account_id": "paper",
        "stock_id": "KRX:005930",
        "side": Side.BUY,
        "order_type": OrderType.LIMIT,
        "quantity": 1,
        "price": Decimal(180000),
    }
    return OrderRequest(**{**base, **overrides})


def test_매수와_매도의_api_id_가_다르다(monkeypatch):
    sent = []
    broker = _order_broker(monkeypatch, {"return_code": 0, "ord_no": "0000139"}, sent)

    broker.submit_order(_request(side=Side.BUY))
    broker.submit_order(_request(side=Side.SELL))

    assert [api_id for api_id, *_ in sent] == ["kt10000", "kt10001"]
    assert all(path == "/api/dostk/ordr" for _, path, *_ in sent)


def test_지정가_바디를_실측_규격대로_보낸다(monkeypatch):
    sent = []
    broker = _order_broker(monkeypatch, {"return_code": 0, "ord_no": "0000139"}, sent)

    broker.submit_order(_request())

    assert sent[0][2] == {
        "dmst_stex_tp": "KRX",
        "stk_cd": "005930",
        "ord_qty": "1",
        "ord_uv": "180000",
        "trde_tp": "0",
    }


def test_시장가는_가격을_빈_값으로_보낸다(monkeypatch):
    sent = []
    broker = _order_broker(monkeypatch, {"return_code": 0, "ord_no": "0000139"}, sent)

    broker.submit_order(_request(order_type=OrderType.MARKET, price=None))

    assert sent[0][2]["trde_tp"] == "3"
    assert sent[0][2]["ord_uv"] == ""


def test_주문에_계좌번호와_client_order_id_를_보내지_않는다(monkeypatch):
    """필수 필드는 다섯이고 그 안에 둘 다 없다 (2026-08-31 실측).

    계좌는 앱키·토큰에 묶이고 client_order_id 는 우리 쪽 멱등성 키다.
    """
    sent = []
    broker = _order_broker(monkeypatch, {"return_code": 0, "ord_no": "0000139"}, sent)

    broker.submit_order(_request())

    body = json.dumps(sent[0][2])
    assert "paper" not in body
    assert "01JABCDEF0000000000000000" not in body


def test_지정가에_가격이_없으면_주문을_내지_않는다(monkeypatch):
    """빈 가격으로 보내면 시장가가 된다. 나가기 전에 막는다."""
    sent = []
    broker = _order_broker(monkeypatch, {"return_code": 0, "ord_no": "0000139"}, sent)

    with pytest.raises(PermanentError):
        broker.submit_order(_request(price=None))

    assert sent == []


def test_거부는_예외가_아니라_rejected_로_온다(monkeypatch):
    """접수 실패가 확정된 응답이다. 호출부가 그대로 DB 에 기록하고 끝낸다."""
    sent = []
    broker = _order_broker(monkeypatch, REJECT_RESPONSE, sent)

    result = broker.submit_order(_request())

    assert result.status == "rejected"
    assert result.broker_order_no is None
    assert result.error_code == "20"
    assert "장종료" in result.error_message
    assert result.client_order_id == "01JABCDEF0000000000000000"


def test_거부_사유를_읽으려_return_code_검사를_끈다(monkeypatch):
    """_request 가 예외를 던져버리면 사유를 OrderResult 에 못 담는다."""
    sent = []
    broker = _order_broker(monkeypatch, REJECT_RESPONSE, sent)

    broker.submit_order(_request())

    assert sent[0][3] == {"check_return_code": False}


def test_접수는_체결_전_상태다(monkeypatch):
    """주문 API 는 접수만 답한다. 체결은 get_order_status 가 준다."""
    sent = []
    broker = _order_broker(monkeypatch, {"return_code": 0, "ord_no": "0000139"}, sent)

    result = broker.submit_order(_request())

    assert result.status == "submitted"
    assert result.filled_qty == 0
    assert result.avg_fill_price is None


def test_주문번호가_없으면_조용히_넘어가지_않는다(caplog):
    """틀린 필드명을 읽으면 추적도 취소도 못 한다. 예외는 안 던진다 —
    주문은 이미 나갔다."""
    with caplog.at_level(logging.ERROR):
        assert KiwoomBroker._order_no({"return_code": 0}) is None

    assert "주문번호" in caplog.text


# ---- 주문 상태 조회 ----

# 2026-08-31 모의투자 ka10076 응답에서 그대로 옮긴 행이다.
# 지정가 250,500 주문이 250,250 에 체결됐다
FILLED_ROW = {
    "ord_no": "0060327",
    "stk_nm": "삼성전자",
    "io_tp_nm": "+매수",
    "ord_pric": "250500",
    "ord_qty": "1",
    "cntr_pric": "250250",
    "cntr_qty": "1",
    "oso_qty": "0",
    "tdy_trde_cmsn": "870",
    "tdy_trde_tax": "0",
    "ord_stt": "체결",
    "trde_tp": "보통",
    "orig_ord_no": "0000000",
    "ord_tm": "101414",
    "stk_cd": "005930",
    "stex_tp": "1",
    "stex_tp_txt": "KRX",
    "sor_yn": "N",
    "stop_pric": "0",
}

# 2026-08-31 모의투자 ka10075 응답. 미체결이 없는 상태다
NO_UNFILLED = {"oso": [], "return_code": 0, "return_msg": " 조회가 완료되었습니다."}


def _status_broker(monkeypatch, responses: dict, sent: list) -> KiwoomBroker:
    broker = KiwoomBroker.__new__(KiwoomBroker)

    def fake_call(api_id, path, body, **extra):
        sent.append((api_id, body))
        return responses[api_id]

    monkeypatch.setattr(broker, "_call", fake_call)
    return broker


def test_체결된_주문을_체결_목록에서_찾는다(monkeypatch):
    sent = []
    broker = _status_broker(
        monkeypatch,
        {"ka10075": NO_UNFILLED, "ka10076": {"cntr": [FILLED_ROW]}},
        sent,
    )

    result = broker.get_order_status("paper", "0060327", "01JABC")

    assert result.status == "filled"
    assert result.filled_qty == 1
    assert result.avg_fill_price == Decimal(250250)
    assert result.broker_order_no == "0060327"
    assert result.client_order_id == "01JABC"


def test_미체결을_먼저_보고_없으면_체결을_본다(monkeypatch):
    sent = []
    broker = _status_broker(
        monkeypatch,
        {"ka10075": NO_UNFILLED, "ka10076": {"cntr": [FILLED_ROW]}},
        sent,
    )

    broker.get_order_status("paper", "0060327", "01JABC")

    assert [api_id for api_id, _ in sent] == ["ka10075", "ka10076"]


def test_미체결에서_찾으면_체결을_부르지_않는다(monkeypatch):
    """호출을 아끼려는 게 아니라, 미체결이 더 최신 상태이기 때문이다."""
    sent = []
    open_row = {**FILLED_ROW, "cntr_qty": "0", "oso_qty": "1"}
    broker = _status_broker(monkeypatch, {"ka10075": {"oso": [open_row]}}, sent)

    result = broker.get_order_status("paper", "0060327", "01JABC")

    assert [api_id for api_id, _ in sent] == ["ka10075"]
    assert result.status == "submitted"
    assert result.filled_qty == 0
    assert result.avg_fill_price is None


def test_부분체결을_수량으로_판정한다(monkeypatch):
    """ord_stt 문자열이 아니라 수량으로 정한다. 어휘를 다 모른다."""
    sent = []
    partial = {**FILLED_ROW, "ord_qty": "10", "cntr_qty": "4", "oso_qty": "6"}
    broker = _status_broker(monkeypatch, {"ka10075": {"oso": [partial]}}, sent)

    result = broker.get_order_status("paper", "0060327", "01JABC")

    assert result.status == "partial"
    assert result.filled_qty == 4


def test_주문번호가_다르면_찾지_않는다(monkeypatch):
    sent = []
    broker = _status_broker(
        monkeypatch,
        {"ka10075": NO_UNFILLED, "ka10076": {"cntr": [FILLED_ROW]}},
        sent,
    )

    with pytest.raises(PermanentError):
        broker.get_order_status("paper", "9999999", "01JABC")


def test_조회에_KRX_거래소구분을_보낸다(monkeypatch):
    """ka10075·ka10076 은 KRX 가 문자열 "1" 이다. kt00018 의 "KRX" 와 다르다."""
    sent = []
    broker = _status_broker(
        monkeypatch,
        {"ka10075": NO_UNFILLED, "ka10076": {"cntr": [FILLED_ROW]}},
        sent,
    )

    broker.get_order_status("paper", "0060327", "01JABC")

    assert all(body["stex_tp"] == "1" for _, body in sent)


def test_한_주문이_여러_행이면_조용히_넘어가지_않는다(monkeypatch, caplog):
    """부분체결에서 나올 수 있다. 그러면 수량 계산이 틀린다."""
    sent = []
    broker = _status_broker(
        monkeypatch, {"ka10075": {"oso": [FILLED_ROW, FILLED_ROW]}}, sent
    )

    with caplog.at_level(logging.ERROR):
        broker.get_order_status("paper", "0060327", "01JABC")

    assert "0060327" in caplog.text
