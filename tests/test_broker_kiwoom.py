# 키움 응답 파싱을 실제 응답 샘플로 고정해 확인한다

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from common.broker.errors import PermanentError, RateLimitError, TransientError
from common.broker.kiwoom import KiwoomBroker, strip_sign, to_amount, to_utc

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


def test_보유종목은_아직_구현하지_않았다():
    """배열 필드를 실측하지 못했다. 추측으로 파서를 쓰지 않는다."""
    broker = KiwoomBroker.__new__(KiwoomBroker)

    with pytest.raises(NotImplementedError, match="실측"):
        broker.get_positions("paper")


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
