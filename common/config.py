# config/ 의 YAML 설정 파일을 읽는다. 비밀값은 여기에 두지 않는다 (common/env.py 담당)

from __future__ import annotations

import pathlib
from typing import Any

import yaml

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"


def load_config(name: str) -> dict[str, Any]:
    """config/{name}.yaml 을 읽는다. 없으면 RuntimeError."""
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        raise RuntimeError(
            f"{path.name} 이 없습니다. config/{name}.example.yaml 을 복사해 채우세요."
        )
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
