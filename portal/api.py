# 조회 API. INTERFACES.md 10장 조회 부분이다. 제어는 control.py 에 있다

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


@api.get("/keywords/surge")
def keywords_surge():
    return jsonify(queries.keywords_surge(_date_arg("date")))


@api.get("/messages")
def messages():
    keyword = request.args.get("keyword", "").strip()
    if not keyword:
        raise BadRequest("keyword 가 필요합니다.")
    return jsonify(queries.messages(keyword, _date_arg("from"), _int_arg("limit")))


@api.get("/disclosures")
def disclosures():
    return jsonify(queries.disclosures(_int_arg("limit")))


@api.post("/keywords/merge")
def keywords_merge():
    """동의어 병합. 조회 전용 포털에서 유일한 쓰기다 (INTERFACES.md 10장).

    파라미터를 바꾸는 것이 아니라 사전을 다듬는 것이다 (PROJECT.md 8.2).
    """
    body = request.get_json(silent=True) or request.form
    try:
        into = int(body["into"])
        from_ids = [int(value) for value in _as_list(body, "from")]
    except (KeyError, TypeError, ValueError):
        raise BadRequest("into 와 from 이 필요합니다.") from None
    if not from_ids:
        raise BadRequest("병합할 키워드가 없습니다.")

    return jsonify(merged=queries.merge(into, from_ids))


@api.get("/trading")
def trading():
    """자동매매 탭 전체 (INTERFACES.md 10장 조회)."""
    return jsonify(queries.trading())


@api.get("/processes")
def processes():
    return jsonify(queries.processes())


@api.get("/events")
def events():
    return jsonify(queries.events(_levels_arg(), _int_arg("limit")))


@api.get("/backtest/runs")
def backtest_runs():
    return jsonify(queries.backtest_runs(_int_arg("limit")))


def _as_list(body, key: str) -> list:
    """JSON 은 배열, 폼은 같은 이름의 값 여러 개로 온다."""
    if hasattr(body, "getlist"):
        return body.getlist(key)
    value = body.get(key)
    return value if isinstance(value, list) else [value]


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
