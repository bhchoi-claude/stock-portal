# 백테스트 CLI 의 파라미터 덮어쓰기. 설정 파일은 건드리지 않는다

import pytest

from backtest.__main__ import _override
from common.config import load_config

PARAMS = load_config("strategy_swing")


def test_override_replaces_one_value():
    changed = _override(PARAMS, ["stop_loss=0.15"])

    assert changed["stop_loss"] == 0.15
    assert changed["ma_exit"] == PARAMS["ma_exit"]  # 나머지는 그대로다


def test_override_does_not_touch_the_loaded_config():
    """설정 파일이 정본이다. 덮어쓰기는 이번 실행에만 산다."""
    _override(PARAMS, ["stop_loss=0.99"])

    assert PARAMS["stop_loss"] == load_config("strategy_swing")["stop_loss"]


def test_types_follow_the_yaml_rules():
    """설정 파일과 같은 방식으로 형을 정한다. 정수는 정수로 온다."""
    changed = _override(PARAMS, ["ma_exit=40", "stop_loss=0.2"])

    assert changed["ma_exit"] == 40
    assert isinstance(changed["ma_exit"], int)
    assert isinstance(changed["stop_loss"], float)


def test_unknown_key_is_rejected():
    """오타가 조용히 새 키를 만들면 안 바뀐 채 바뀐 줄 알게 된다."""
    with pytest.raises(ValueError, match="없는 파라미터"):
        _override(PARAMS, ["stoploss=0.15"])


def test_missing_equals_is_rejected():
    with pytest.raises(ValueError, match="KEY=VALUE"):
        _override(PARAMS, ["stop_loss"])


def test_several_overrides_apply_together():
    changed = _override(PARAMS, ["stop_loss=0.15", "breakout_days=20"])

    assert changed["stop_loss"] == 0.15
    assert changed["breakout_days"] == 20


def test_no_override_returns_the_same_values():
    assert _override(PARAMS, []) == PARAMS
