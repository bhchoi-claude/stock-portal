# 일 1회 배치가 휴장일과 실패를 구분하는지 확인한다

import pytest

from collectors.market import EXIT_HOLIDAY, daily


def steps(*results):
    """(이름, 종료코드 또는 예외) 목록을 STEPS 형태로 만든다."""

    def entry(result):
        def run(argv):
            if isinstance(result, Exception):
                raise result
            return result

        return run

    return tuple((f"단계{i}", entry(r)) for i, r in enumerate(results))


def test_모두_성공하면_실패가_없다(monkeypatch):
    monkeypatch.setattr(daily, "STEPS", steps(0, 0, 0))

    assert daily.run_steps("20260826") == (False, [])


def test_휴장일이면_뒤_단계를_돌리지_않는다(monkeypatch):
    called = []
    monkeypatch.setattr(
        daily,
        "STEPS",
        (
            ("첫째", lambda argv: EXIT_HOLIDAY),
            ("둘째", lambda argv: called.append("둘째") or 0),
        ),
    )

    holiday, failed = daily.run_steps("20260815")

    assert holiday is True
    assert failed == []
    assert called == []


def test_한_단계가_실패해도_다음을_진행한다(monkeypatch):
    called = []
    monkeypatch.setattr(
        daily,
        "STEPS",
        (
            ("첫째", lambda argv: 1),
            ("둘째", lambda argv: called.append("둘째") or 0),
        ),
    )

    holiday, failed = daily.run_steps("20260826")

    assert holiday is False
    assert failed == ["첫째"]
    assert called == ["둘째"]


def test_예외도_실패로_잡는다(monkeypatch):
    # 한 수집기의 예외가 다른 수집기로 번지면 안 된다
    monkeypatch.setattr(daily, "STEPS", steps(RuntimeError("네트워크"), 0))

    holiday, failed = daily.run_steps("20260826")

    assert holiday is False
    assert failed == ["단계0"]


def test_기준일자를_그대로_넘긴다(monkeypatch):
    seen = []
    monkeypatch.setattr(
        daily, "STEPS", (("첫째", lambda argv: seen.append(argv[1]) or 0),)
    )

    daily.run_steps("20260826")

    assert seen == ["20260826"]


@pytest.mark.parametrize(
    "index, name",
    [(0, "종목 마스터"), (1, "일봉"), (2, "폐지일 정밀화")],
)
def test_단계_순서(index, name):
    # 신규 상장이 stock 에 있어야 시세가 FK 를 통과하고,
    # 폐지일 정밀화는 그날 일봉이 들어온 뒤라야 맞는 값을 본다
    assert daily.STEPS[index][0] == name


def test_시세가_없으면_휴장일이다(monkeypatch):
    monkeypatch.setattr(daily, "fetch", lambda path, api_id, bas_dd: [])

    assert daily.is_holiday("20260815") is True


def test_시세가_있으면_거래일이다(monkeypatch):
    monkeypatch.setattr(
        daily, "fetch", lambda path, api_id, bas_dd: [{"ISU_CD": "005930"}]
    )

    assert daily.is_holiday("20260826") is False


def test_휴장일_판정에_시세_api_를_쓴다(monkeypatch):
    # 종목기본정보는 휴장일에도 응답하므로 판정에 쓸 수 없다
    seen = {}
    monkeypatch.setattr(
        daily,
        "fetch",
        lambda path, api_id, bas_dd: seen.update(api_id=api_id) or [],
    )

    daily.is_holiday("20260815")

    assert seen["api_id"] == "stk_bydd_trd"
