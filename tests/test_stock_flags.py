# 관리종목·거래정지 플래그 파싱과 변경 감지를 확인한다

from datetime import date

from collectors.market.stock_flags import detect
from common.broker.kiwoom import KiwoomBroker
from common.types import StockState

# 2026-08-29 모의투자 ka10099 응답에서 그대로 옮긴 행들이다
NORMAL = {
    "code": "000020",
    "name": "동화약품",
    "auditInfo": "정상",
    "state": "증거금40%|담보대출|신용가능",
}
# auditInfo 는 값이 하나뿐이라 거래정지가 관리종목을 덮는다.
# 이 종목은 KRX 소속부가 관리종목인데 auditInfo 에는 거래정지만 남는다
MANAGED_AND_SUSPENDED = {
    "code": "001000",
    "name": "신라섬유",
    "auditInfo": "거래정지",
    "state": "관리종목",
}
MANAGED_ONLY = {
    "code": "032685",
    "name": "소프트센우",
    "auditInfo": "관리종목",
    "state": "관리종목|증거금100%",
}


def test_정상_종목은_두_플래그가_모두_거짓이다():
    state = KiwoomBroker._to_state(NORMAL)

    assert state.stock_id == "KRX:000020"
    assert state.is_managed is False
    assert state.is_suspended is False


def test_거래정지가_관리종목을_덮어도_둘_다_잡는다():
    # auditInfo 만 보면 관리종목을 놓친다. state 를 함께 봐야 한다
    state = KiwoomBroker._to_state(MANAGED_AND_SUSPENDED)

    assert state.is_managed is True
    assert state.is_suspended is True


def test_관리종목이지만_거래는_되는_경우():
    state = KiwoomBroker._to_state(MANAGED_ONLY)

    assert state.is_managed is True
    assert state.is_suspended is False


def test_state_는_토큰으로_끊어_본다():
    # '관리종목' 을 부분문자열로 담은 다른 토큰에 걸리지 않는다
    row = {"code": "000660", "auditInfo": "정상", "state": "투자주의환기종목"}

    assert KiwoomBroker._to_state(row).is_managed is False


def test_바뀐_종목만_고른다():
    known = {
        "KRX:000020": (date(2026, 8, 1), False, False),
        "KRX:001000": (date(2026, 8, 1), True, False),
    }
    states = [
        StockState("KRX:000020", is_managed=False, is_suspended=False),
        StockState("KRX:001000", is_managed=True, is_suspended=True),
    ]

    changed = detect(states, known)

    assert [s.stock_id for s in changed] == ["KRX:001000"]


def test_우리_DB_에_없는_종목은_무시한다():
    # ka10099 는 ETF·ETN 도 함께 준다. stock 에 없으면 볼 것이 없다
    known = {"KRX:000020": (date(2026, 8, 1), False, False)}
    states = [StockState("KRX:069500", is_managed=True, is_suspended=False)]

    assert detect(states, known) == []


def test_폐지_종목은_열린_행이_없어_빠진다():
    # 폐지 시 close_stock_status 로 행이 닫힌다. known 에 안 들어온다
    known: dict[str, tuple[date, bool, bool]] = {}
    states = [StockState("KRX:000020", is_managed=True, is_suspended=True)]

    assert detect(states, known) == []
