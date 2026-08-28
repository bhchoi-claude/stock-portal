# 국면 판정, 특히 지표 결측 시 정규화를 확인한다 (CLAUDE.md 필수 테스트)

from datetime import date
from decimal import Decimal

import pytest

from collectors.market.regime import (
    InsufficientData,
    RegimeResult,
    evaluate,
    indicator_score,
    is_fresh,
    weighted_mean,
)
from common.config import load_config
from common.types import Regime

AS_OF = date(2026, 8, 28)

RULES = {
    "version": "test",
    "layers": {
        "a": {
            "weight": 0.5,
            "indicators": [
                {"code": "X", "weight": 0.5, "thresholds": {"danger": 0, "safe": 10}},
                {"code": "Y", "weight": 0.5, "thresholds": {"danger": 0, "safe": 10}},
            ],
        },
        "b": {
            "weight": 0.5,
            "indicators": [
                {"code": "Z", "weight": 1.0, "thresholds": {"danger": 0, "safe": 10}}
            ],
        },
    },
    "output": {"danger_below": -0.3, "safe_above": 0.3},
}


def v(value, day=AS_OF):
    return (day, Decimal(str(value)))


def test_임계값_사이를_선형으로_옮긴다():
    assert indicator_score(Decimal(0), Decimal(0), Decimal(10)) == Decimal(-1)
    assert indicator_score(Decimal(5), Decimal(0), Decimal(10)) == Decimal(0)
    assert indicator_score(Decimal(10), Decimal(0), Decimal(10)) == Decimal(1)


def test_범위_밖은_잘라낸다():
    assert indicator_score(Decimal(-5), Decimal(0), Decimal(10)) == Decimal(-1)
    assert indicator_score(Decimal(99), Decimal(0), Decimal(10)) == Decimal(1)


def test_값이_클수록_위험한_지표는_방향이_뒤집힌다():
    # VKOSPI 처럼 danger 가 safe 보다 큰 경우
    assert indicator_score(Decimal(30), Decimal(25), Decimal(15)) == Decimal(-1)
    assert indicator_score(Decimal(20), Decimal(25), Decimal(15)) == Decimal(0)
    assert indicator_score(Decimal(10), Decimal(25), Decimal(15)) == Decimal(1)


def test_임계값이_같으면_거부한다():
    with pytest.raises(ValueError):
        indicator_score(Decimal(1), Decimal(5), Decimal(5))


def test_결측_지표는_빼고_남은_가중치로_정규화한다():
    # X 만 있고 Y 가 없다. 계층 a 의 점수는 X 하나로 결정돼야 한다.
    # Y 를 0 으로 넣으면 (1 + 0) / 2 = 0.5 가 되어 틀린다
    result = evaluate(RULES, {"X": v(10), "Z": v(10)}, AS_OF)

    assert result.layer_scores["a"] == Decimal(1)
    assert result.score == Decimal(1)


def test_결측을_영으로_취급하지_않는다():
    with_both = evaluate(RULES, {"X": v(10), "Y": v(10), "Z": v(0)}, AS_OF)
    with_one = evaluate(RULES, {"X": v(10), "Z": v(0)}, AS_OF)

    # 계층 a 는 둘 다 1 이므로 하나가 빠져도 같아야 한다
    assert with_both.layer_scores["a"] == with_one.layer_scores["a"] == Decimal(1)
    assert with_both.score == with_one.score


def test_계층_전체가_결측이면_그_계층도_뺀다():
    # 계층 b 가 통째로 없다. 계층 a 만으로 판정한다
    result = evaluate(RULES, {"X": v(10), "Y": v(10)}, AS_OF)

    assert "b" not in result.layer_scores
    assert result.score == Decimal(1)


def test_쓸_수_있는_지표가_없으면_판정하지_않는다():
    # 중립으로 적으면 '판단했다' 는 거짓 기록이 남는다
    with pytest.raises(InsufficientData):
        evaluate(RULES, {}, AS_OF)


def test_묵은_값은_빼고_센다():
    rules = {
        **RULES,
        "layers": {
            "a": {
                "weight": 1.0,
                "indicators": [
                    {
                        "code": "X",
                        "weight": 1.0,
                        "max_age_days": 5,
                        "thresholds": {"danger": 0, "safe": 10},
                    }
                ],
            }
        },
    }

    with pytest.raises(InsufficientData):
        evaluate(rules, {"X": v(10, date(2026, 1, 1))}, AS_OF)


def test_max_age_days_가_없으면_묵어도_쓴다():
    assert is_fresh(date(2020, 1, 1), AS_OF, None) is True
    assert is_fresh(date(2020, 1, 1), AS_OF, 5) is False


def test_점수로_국면을_가른다():
    danger = evaluate(RULES, {"X": v(0), "Y": v(0), "Z": v(0)}, AS_OF)
    neutral = evaluate(RULES, {"X": v(5), "Y": v(5), "Z": v(5)}, AS_OF)
    safe = evaluate(RULES, {"X": v(10), "Y": v(10), "Z": v(10)}, AS_OF)

    assert danger.regime is Regime.DANGER
    assert neutral.regime is Regime.NEUTRAL
    assert safe.regime is Regime.SAFE


def test_판정에_쓴_값만_스냅샷에_남는다():
    result = evaluate(RULES, {"X": v(10), "Z": v(3)}, AS_OF)

    assert set(result.indicators) == {"X", "Z"}


def test_규칙_버전을_결과에_담는다():
    # market_regime.rule_version 으로 변경 전후를 구분한다
    assert evaluate(RULES, {"X": v(1)}, AS_OF).rule_version == "test"


def test_가중평균은_남은_가중치로_나눈다():
    assert weighted_mean([(Decimal("0.5"), Decimal(1))]) == Decimal(1)
    assert weighted_mean(
        [(Decimal("0.5"), Decimal(1)), (Decimal("0.5"), Decimal(-1))]
    ) == Decimal(0)


def test_실제_규칙_파일이_읽히고_형식이_맞는다():
    rules = load_config("regime_rules")

    assert rules["version"]
    assert set(rules["layers"]) == {"sentiment", "risk", "position", "fundamental"}
    for layer, spec in rules["layers"].items():
        assert spec["weight"] > 0, layer
        for item in spec["indicators"]:
            assert item["thresholds"]["danger"] != item["thresholds"]["safe"], item[
                "code"
            ]


def test_실제_규칙_파일의_계층_가중치_합이_일이다():
    rules = load_config("regime_rules")

    assert sum(s["weight"] for s in rules["layers"].values()) == pytest.approx(1.0)


def test_실제_규칙_파일의_지표_가중치_합이_계층마다_일이다():
    rules = load_config("regime_rules")

    for layer, spec in rules["layers"].items():
        total = sum(i["weight"] for i in spec["indicators"])
        assert total == pytest.approx(1.0), layer


def test_실제_규칙_파일로_판정이_돈다():
    rules = load_config("regime_rules")
    values = {"VKOSPI": v(30), "USDKRW": v(1500)}

    result = evaluate(rules, values, AS_OF)

    assert isinstance(result, RegimeResult)
    # 위험 지표 둘만 최악이면 risk 계층 점수는 -1 이다
    assert result.layer_scores["risk"] == Decimal(-1)
    assert result.score == Decimal(-1)
