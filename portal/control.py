# 제어 API. command 테이블에 기록만 한다. 엔진을 직접 부르지 않는다 (CLAUDE.md 8)

from __future__ import annotations

from datetime import date
from typing import Any

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import BadRequest, NotFound

from common.config import load_config
from common.db.commands import enqueue
from common.db.events import log_event
from common.db.filters import TYPES, add_filter, remove_filter

from .queries import read_cursor

control = Blueprint("control", __name__, url_prefix="/api")

# INTERFACES.md 10.2. 우발적 호출을 막는다
LIQUIDATE_TOKEN = "LIQUIDATE"

PROCESS = "portal"


@control.post("/control/halt-entry")
def halt_entry():
    """신규 진입 차단. **청산은 계속 돈다** (INTERFACES.md 4.1).

    확인 토큰을 요구하지 않는다. 안전한 쪽으로 가는 조작이라 우발적으로
    눌려도 손해가 없다. 되돌리는 것은 엔진 재시작이다.
    """
    return _issue("halt_entry")


@control.post("/control/liquidate-all")
def liquidate_all():
    """전량 청산. **확인 토큰을 요구한다** (INTERFACES.md 10.2).

    시장가 매도가 나간다. 되돌릴 수 없다.
    """
    body = _body()
    if body.get("confirm") != LIQUIDATE_TOKEN:
        raise BadRequest(f'confirm 이 "{LIQUIDATE_TOKEN}" 이어야 합니다.')
    return _issue("liquidate_all", reason=_reason(body))


@control.post("/control/engine/<name>/stop")
def stop_engine(name: str):
    """엔진 정지. **화면에는 버튼을 두지 않는다.**

    되살리려면 서버에서 `systemctl start` 를 쳐야 한다. 포털이 프로세스를
    띄울 수는 없다 — 엔진이 죽어 있으면 `command` 를 폴링할 주체가 없고,
    포털이 엔진을 직접 부르는 것은 규약 위반이다 (CLAUDE.md 8).

    `INTERFACES.md` 10장의 `.../start` 는 같은 이유로 만들지 않는다.
    """
    body = _body()
    if body.get("confirm") != name:
        raise BadRequest(f"confirm 이 프로세스 이름({name})이어야 합니다.")
    return _issue("stop", target=name, reason=_reason(body))


@control.post("/positions/<account>/<stock_id>/close")
def close_position(account: str, stock_id: str):
    """종목 하나를 시장가로 판다.

    계좌를 경로에서 받지만 엔진이 자기 계좌만 처리한다. 다른 계좌를 넣으면
    명령이 그 엔진에게 가지 않아 아무 일도 일어나지 않는다.
    """
    engine = _engine()
    if account != engine["account_id"]:
        raise NotFound(f"모르는 계좌입니다: {account}")

    return _issue(
        "close_position",
        params={"stock_id": stock_id},
        reason=_reason(_body()),
    )


@control.post("/filters")
def add():
    """제외·허용 목록에 넣는다.

    **엔진은 `block` 만 본다.** `allow`(화이트리스트)는 모드 전환 설정이
    없어 아직 아무 일도 하지 않는다. 넣을 수는 있게 두되 화면이 그렇게
    표시한다.
    """
    body = _body()
    stock_id = str(body.get("stock_id") or "").strip()
    if not stock_id:
        raise BadRequest("stock_id 가 필요합니다.")

    filter_type = str(body.get("filter_type") or "block")
    if filter_type not in TYPES:
        raise BadRequest(f"filter_type 은 {' 또는 '.join(TYPES)} 여야 합니다.")

    strategy = str(body.get("strategy") or _engine()["strategy"])
    until = _until(body.get("until_date"))

    with read_cursor() as cur:
        filter_id = add_filter(
            cur,
            stock_id=stock_id,
            strategy=strategy,
            filter_type=filter_type,
            reason=(str(body.get("reason")).strip() or None)
            if body.get("reason")
            else None,
            until_date=until,
        )
        log_event(
            cur,
            PROCESS,
            "INFO",
            f"{stock_id} 를 {filter_type} 목록에 넣었습니다",
            category="trade",
            detail={"filter_id": filter_id, "strategy": strategy},
        )
    return jsonify(filter_id=filter_id)


@control.delete("/filters/<int:filter_id>")
def remove(filter_id: int):
    with read_cursor() as cur:
        if not remove_filter(cur, filter_id):
            raise NotFound(f"없는 항목입니다: {filter_id}")
        log_event(
            cur,
            PROCESS,
            "INFO",
            f"목록 {filter_id} 을 지웠습니다",
            category="trade",
        )
    return jsonify(removed=filter_id)


# --- 보조 --------------------------------------------------------------------


def _issue(
    action: str,
    *,
    target: str | None = None,
    params: dict[str, Any] | None = None,
    reason: str | None = None,
):
    """명령을 넣고 `event_log` 에 남긴다.

    **모든 제어 요청은 `event_log` 에 남긴다** (INTERFACES.md 10.2).
    명령이 처리됐는지는 `command.status` 로 따로 보인다. 둘은 다른 것이다 —
    이쪽은 '눌렀다', 저쪽은 '엔진이 했다' 다.
    """
    engine = _engine()
    with read_cursor() as cur:
        command_id = enqueue(
            cur,
            target=target or engine["process_name"],
            action=action,
            params=params,
            issued_by=PROCESS,
        )
        log_event(
            cur,
            PROCESS,
            "WARN",
            f"{action} 명령을 넣었습니다",
            category="trade",
            detail={"command_id": command_id, "params": params, "reason": reason},
        )
    return jsonify(command_id=command_id)


def _engine() -> dict[str, Any]:
    return load_config("engine")["swing"]


def _body() -> dict[str, Any]:
    """JSON 과 폼 둘 다 받는다. 화면은 폼으로 보낸다."""
    if request.is_json:
        body = request.get_json(silent=True)
        return body if isinstance(body, dict) else {}
    return request.form.to_dict()


def _reason(body: dict[str, Any]) -> str | None:
    reason = str(body.get("reason") or "").strip()
    return reason or None


def _until(raw: Any) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        raise BadRequest(f"until_date 는 YYYY-MM-DD 형식이어야 합니다: {raw}") from None
