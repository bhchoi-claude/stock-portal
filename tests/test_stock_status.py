# 상장·폐지·이전상장 감지를 확인한다

from collectors.market.stock_status import detect
from common.db.models import Stock


def stock(code: str, board: str = "KOSPI") -> Stock:
    return Stock(
        stock_id=f"KRX:{code}", exchange="KRX", code=code, board=board, name="테스트"
    )


def test_변경이_없으면_아무것도_감지하지_않는다():
    incoming = [stock("005930"), stock("035720", "KOSDAQ")]
    known = {"KRX:005930": "KOSPI", "KRX:035720": "KOSDAQ"}

    changes = detect(incoming, known, set())

    assert changes.listed == []
    assert changes.delisted == []
    assert changes.moved == []


def test_새로_나타나면_상장이다():
    changes = detect([stock("005930"), stock("999990")], {"KRX:005930": "KOSPI"}, set())

    assert changes.listed == ["KRX:999990"]
    assert changes.delisted == []


def test_사라지면_폐지다():
    # 이전 적재와의 차집합으로 감지한다
    changes = detect(
        [stock("005930")], {"KRX:005930": "KOSPI", "KRX:001880": "KOSPI"}, set()
    )

    assert changes.delisted == ["KRX:001880"]
    assert changes.listed == []


def test_시장이_바뀌면_이전상장이다():
    changes = detect([stock("035720", "KOSPI")], {"KRX:035720": "KOSDAQ"}, set())

    assert changes.moved == ["KRX:035720"]
    assert changes.listed == []
    assert changes.delisted == []


def test_폐지됐던_종목이_다시_나타나면_상장이_아니다():
    # 신규 상장으로 세면 listed_at 과 이력이 어긋난다
    changes = detect([stock("001880")], {}, {"KRX:001880"})

    assert changes.relisted == ["KRX:001880"]
    assert changes.listed == []


def test_폐지_종목은_known_에_없어도_폐지로_다시_세지_않는다():
    # known 은 상장중인 종목만 담는다. 폐지 종목이 계속 폐지로 잡히면 안 된다
    changes = detect([stock("005930")], {"KRX:005930": "KOSPI"}, {"KRX:001880"})

    assert changes.delisted == []
