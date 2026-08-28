# 상장주식수 변화에서 조정 이벤트를 찾는 규칙을 확인한다

from datetime import date
from decimal import Decimal

from collectors.market.corporate_action import detect_actions

DAY = date(2024, 11, 11)
JUMPED = {"KRX:102280", "KRX:065560", "KRX:264450", "KRX:X"}


def test_주식수가_줄면_조정_이벤트다():
    # 쌍방울 50:1 감자. 262,592,129 -> 5,251,842
    actions, increased = detect_actions(
        DAY, {"KRX:102280": 262592129}, {"KRX:102280": 5251842}, JUMPED
    )

    assert len(actions) == 1
    assert actions[0].adjusts_price is True
    assert actions[0].effective_date == DAY
    assert round(actions[0].ratio, 4) == Decimal("0.0200")
    assert increased == []


def test_비율은_이후를_이전으로_나눈_값이다():
    actions, _ = detect_actions(DAY, {"KRX:X": 100}, {"KRX:X": 50}, JUMPED)

    assert actions[0].ratio == Decimal("0.5")


def test_주식수가_그대로면_가격이_움직여도_이벤트가_아니다():
    # 녹원씨엔아이. 주식수 불변인데 종가가 1/49 이 됐다. 거래정지 해제다
    actions, increased = detect_actions(
        DAY, {"KRX:065560": 16440776}, {"KRX:065560": 16440776}, JUMPED
    )

    assert actions == []
    assert increased == []


def test_주식수가_늘면_보류한다():
    # 무상증자와 유상증자가 섞여 있어 adjusts_price 를 정할 수 없다
    actions, increased = detect_actions(DAY, {"KRX:X": 100}, {"KRX:X": 200}, JUMPED)

    assert actions == []
    assert increased == ["KRX:X"]


def test_신규_종목은_이벤트가_아니다():
    # 이전 날에 없던 종목은 상장이지 조정이 아니다
    actions, increased = detect_actions(DAY, {}, {"KRX:X": 100}, JUMPED)

    assert actions == []
    assert increased == []


def test_사라진_종목은_보지_않는다():
    # 폐지 종목이 조정 이벤트로 잡히면 안 된다
    actions, increased = detect_actions(DAY, {"KRX:X": 100}, {}, JUMPED)

    assert actions == []
    assert increased == []


def test_상세에_주식수를_남긴다():
    # 감자와 액면병합을 한 갈래로 묶었으므로 근거를 남겨야 한다
    actions, _ = detect_actions(DAY, {"KRX:X": 100}, {"KRX:X": 50}, JUMPED)

    assert actions[0].detail == {
        "shares_before": 100,
        "shares_after": 50,
        "simple_ratio": True,
    }


def test_가격이_점프하지_않으면_이벤트가_아니다():
    # 자기주식 소각. 주식수는 3% 줄지만 가격은 제한폭 안에 있다
    actions, increased = detect_actions(
        DAY, {"KRX:264450": 10245706}, {"KRX:264450": 9945589}, set()
    )

    assert actions == []
    assert increased == []


def test_주식수와_가격이_모두_움직여야_이벤트다():
    # 가격만 점프하고 주식수가 그대로면 거래정지 해제다
    actions, _ = detect_actions(
        DAY, {"KRX:065560": 16440776}, {"KRX:065560": 16440776}, {"KRX:065560"}
    )

    assert actions == []


def test_단순_분수면_가격_조정_대상이다():
    # 5:1 감자
    actions, _ = detect_actions(DAY, {"KRX:X": 500}, {"KRX:X": 100}, JUMPED)

    assert actions[0].adjusts_price is True
    assert actions[0].detail["simple_ratio"] is True


def test_임의_비율이면_기록만_하고_조정하지_않는다():
    # 코스온 2023-10-11. 23,940,660 -> 22,450,397 은 1/비율이 1.0664 다.
    # 자기주식 소각이고 가격 조정 대상이 아니다
    actions, _ = detect_actions(
        DAY, {"KRX:069110": 23940660}, {"KRX:069110": 22450397}, {"KRX:069110"}
    )

    assert len(actions) == 1
    assert actions[0].adjusts_price is False


def test_인적분할도_조정하지_않는다():
    # 삼성바이오로직스 2025-11-24. 1/비율 1.5375.
    # 주식수 비는 인적분할의 가격 조정 비율이 아니다
    actions, _ = detect_actions(
        DAY, {"KRX:207940": 71174000}, {"KRX:207940": 46292000}, {"KRX:207940"}
    )

    assert actions[0].adjusts_price is False


def test_이분의오도_단순_분수다():
    # 0.4 = 2/5. 분모 4 이하로 표현되는 5/2 다
    actions, _ = detect_actions(DAY, {"KRX:X": 500}, {"KRX:X": 200}, JUMPED)

    assert actions[0].adjusts_price is True
