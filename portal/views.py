# 화면. 템플릿에 넘기는 값은 API 와 같은 조회 함수에서 온다

from __future__ import annotations

from datetime import date

from flask import Blueprint, render_template, request

from . import queries

views = Blueprint("views", __name__)

# 운영·로그 탭에서 볼 등급. 정상 동작 기록까지 섞으면 에러가 묻힌다
OPS_LEVELS = ["ERROR", "CRITICAL"]


@views.get("/")
def dashboard():
    return render_template(
        "dashboard.html", active="dashboard", data=queries.dashboard()
    )


@views.get("/market")
def market():
    return render_template(
        "market.html",
        active="market",
        indicators=queries.indicators(),
        history=queries.regime_range(None, None),
    )


@views.get("/news")
def news():
    day = _date_arg("date")
    surge = queries.keywords_surge(day)
    term = request.args.get("keyword", "").strip()
    return render_template(
        "news.html",
        active="news",
        surge=surge,
        channels=queries.channels(),
        term=term,
        messages=queries.messages(term, None, None) if term else None,
    )


@views.get("/ops")
def ops():
    return render_template(
        "ops.html",
        active="ops",
        processes=queries.processes(),
        events=queries.events(OPS_LEVELS, None),
    )


def _date_arg(name: str) -> date | None:
    raw = request.args.get(name)
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None
