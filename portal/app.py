# 포털 진입점. Flask 앱을 만든다

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from .api import api
from .views import views

SEOUL = ZoneInfo("Asia/Seoul")


def create_app() -> Flask:
    """조회 전용 앱. 제어 엔드포인트는 Phase 8 에 붙는다."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    app = Flask(__name__)
    app.register_blueprint(api)
    app.register_blueprint(views)
    app.register_error_handler(HTTPException, _as_json_if_api)
    app.add_template_filter(kst, "kst")
    app.add_template_filter(num, "num")
    app.add_template_filter(pct, "pct")
    return app


def kst(value: str | None) -> str:
    """UTC 로 저장된 시각을 한국 시각으로 보여준다. 저장은 UTC 그대로다."""
    if not value:
        return "-"
    return datetime.fromisoformat(value).astimezone(SEOUL).strftime("%m-%d %H:%M")


def num(value: str | None) -> str:
    """NUMERIC 은 소수 6자리로 온다. 뒤에 붙은 0 을 떼고 천 단위를 끊는다."""
    if value is None:
        return "-"
    text = format(Decimal(value), ",f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def pct(value: str | None) -> str:
    """변화율을 백분율로 보여준다."""
    if value is None:
        return "-"
    return f"{Decimal(value) * 100:+.2f}%"


def _as_json_if_api(error: HTTPException):
    """/api 아래의 오류는 JSON 으로 낸다. 화면 쪽은 Flask 기본 페이지를 쓴다."""
    if not request.path.startswith("/api/"):
        return error
    return jsonify(error=error.description), error.code
