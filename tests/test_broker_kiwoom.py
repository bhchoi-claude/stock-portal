# 키움 응답 파싱을 실제 응답 샘플로 고정해 확인한다

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from common.broker.errors import PermanentError, RateLimitError, TransientError
from common.broker.kiwoom import KiwoomBroker, strip_sign, to_utc

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
