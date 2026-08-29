# 조회 API. INTERFACES.md 10장 조회 부분이다. 쓰기 엔드포인트는 두지 않는다

from __future__ import annotations

from datetime import date

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import BadRequest

from . import queries

api = Blueprint("api", __name__, url_prefix="/api")

LEVELS = {"INFO", "WARN", "ERROR", "CRITICAL"}


@api.get("/dashboard")
def dashboard():
    return jsonify(queries.dashboard())


@api.get("/regime/current")
def regime_current():
    return jsonify(queries.regime_now())


@api.get("/regime/history")
def regime_history():
    return jsonify(queries.regime_range(_date_arg("from"), _date_arg("to")))


@api.get("/indicators")
def indicators():
    return jsonify(queries.indicators())


@api.get("/processes")
def processes():
    return jsonify(queries.processes())


@api.get("/events")
def events():
    return jsonify(queries.events(_levels_arg(), _int_arg("limit")))


def _date_arg(name: str) -> date | None:
    raw = request.args.get(name)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise BadRequest(f"{name} 은 YYYY-MM-DD 형식이어야 합니다: {raw}") from None


def _int_arg(name: str) -> int | None:
    raw = request.args.get(name)
    if not raw:
        return None
    if not raw.isdigit() or int(raw) == 0:
        raise BadRequest(f"{name} 은 1 이상의 정수여야 합니다: {raw}")
    return int(raw)


def _levels_arg() -> list[str] | None:
    """`?level=ERROR,CRITICAL` 처럼 쉼표로 받는다."""
    raw = request.args.get("level")
    if not raw:
        return None
    levels = [part.strip().upper() for part in raw.split(",") if part.strip()]
    unknown = [level for level in levels if level not in LEVELS]
    if unknown:
        raise BadRequest(f"모르는 level 입니다: {', '.join(unknown)}")
    return levels
