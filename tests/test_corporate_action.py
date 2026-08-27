# 상장주식수 변화에서 조정 이벤트를 찾는 규칙을 확인한다

from datetime import date
from decimal import Decimal

from collectors.market.corporate_action import detect_actions

DAY = date(2024, 11, 11)


def test_주식수가_줄면_조정_이벤트다():
    # 쌍방울 50:1 감자. 262,592,129 -> 5,251,842
    actions, increased = detect_actions(
        DAY, {"KRX:102280": 262592129}, {"KRX:102280": 5251842}
    )

    assert len(actions) == 1
    assert actions[0].adjusts_price is True
    assert actions[0].effective_date == DAY
    assert round(actions[0].ratio, 4) == Decimal("0.0200")
    assert increased == []


def test_비율은_이후를_이전으로_나눈_값이다():
    actions, _ = detect_actions(DAY, {"KRX:X": 100}, {"KRX:X": 50})

    assert actions[0].ratio == Decimal("0.5")


def test_주식수가_그대로면_가격이_움직여도_이벤트가_아니다():
    # 녹원씨엔아이. 주식수 불변인데 종가가 1/49 이 됐다. 거래정지 해제다
    actions, increased = detect_actions(
        DAY, {"KRX:065560": 16440776}, {"KRX:065560": 16440776}
    )

    assert actions == []
    assert increased == []


def test_주식수가_늘면_보류한다():
    # 무상증자와 유상증자가 섞여 있어 adjusts_price 를 정할 수 없다
    actions, increased = detect_actions(DAY, {"KRX:X": 100}, {"KRX:X": 200})

    assert actions == []
    assert increased == ["KRX:X"]


def test_신규_종목은_이벤트가_아니다():
    # 이전 날에 없던 종목은 상장이지 조정이 아니다
    actions, increased = detect_actions(DAY, {}, {"KRX:X": 100})

    assert actions == []
    assert increased == []


def test_사라진_종목은_보지_않는다():
    # 폐지 종목이 조정 이벤트로 잡히면 안 된다
    actions, increased = detect_actions(DAY, {"KRX:X": 100}, {})

    assert actions == []
    assert increased == []


def test_상세에_주식수를_남긴다():
    # 감자와 액면병합을 한 갈래로 묶었으므로 근거를 남겨야 한다
    actions, _ = detect_actions(DAY, {"KRX:X": 100}, {"KRX:X": 50})

    assert actions[0].detail == {"shares_before": 100, "shares_after": 50}
