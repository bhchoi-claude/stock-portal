# market_regime(국면 판정 이력) 테이블 접근 함수

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

# indicator_value 에서 읽을 수 있는 열. 설정 파일의 metric 을 그대로 SQL 에
# 넣지 않고 이 목록으로 거른다
METRICS = {"value", "change_rate"}


def value_as_of(
    cur: psycopg.Cursor, code: str, metric: str, as_of: date
) -> tuple[date, Decimal] | None:
    """기준일 이전의 가장 최근 지표값. 없으면 None.

    metric 은 SQL 에 그대로 들어가므로 METRICS 로 먼저 거른다.

    월간 지표는 월초에만 값이 있으므로 '이전 중 최신' 으로 찾는다.
    너무 묵은 값인지는 판정 쪽에서 `max_age_days` 로 거른다.
    """
    if metric not in METRICS:
        raise ValueError(f"모르는 metric 입니다: {metric}")

    cur.execute(
        f"""
        SELECT period_date, {metric} FROM indicator_value
        WHERE indicator_code = %s AND period_date <= %s AND {metric} IS NOT NULL
        ORDER BY period_date DESC LIMIT 1
        """,
        (code, as_of),
    )
    row = cur.fetchone()
    return (row[0], row[1]) if row else None


def upsert_market_regime(
    cur: psycopg.Cursor,
    trade_date: date,
    regime: str,
    score: Decimal,
    layer_scores: dict[str, Any],
    indicators: dict[str, Any],
    rule_version: str,
) -> int:
    """판정을 기록한다. 수동 override 행은 덮어쓰지 않는다.

    `is_override = TRUE` 면 자동 판정보다 사람의 판단이 우선한다
    (INTERFACES.md 8.3). 해제도 수동이다.
    """
    cur.execute(
        """
        INSERT INTO market_regime (
            trade_date, regime, score, layer_scores, indicators, rule_version
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (trade_date) DO UPDATE SET
            regime       = EXCLUDED.regime,
            score        = EXCLUDED.score,
            layer_scores = EXCLUDED.layer_scores,
            indicators   = EXCLUDED.indicators,
            rule_version = EXCLUDED.rule_version
        WHERE NOT market_regime.is_override
        """,
        (
            trade_date,
            regime,
            score,
            Jsonb(_stringify(layer_scores)),
            Jsonb(_stringify(indicators)),
            rule_version,
        ),
    )
    return cur.rowcount


def previous_regime(cur: psycopg.Cursor, before: date) -> str | None:
    """직전 거래일의 국면. 전환 여부를 판단하는 데 쓴다."""
    cur.execute(
        "SELECT regime FROM market_regime WHERE trade_date < %s"
        " ORDER BY trade_date DESC LIMIT 1",
        (before,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def is_override(cur: psycopg.Cursor, trade_date: date) -> bool:
    """그날 판정이 수동으로 고정돼 있는지."""
    cur.execute(
        "SELECT is_override FROM market_regime WHERE trade_date = %s", (trade_date,)
    )
    row = cur.fetchone()
    return bool(row and row[0])


def _stringify(values: dict[str, Any]) -> dict[str, str]:
    """Decimal 을 문자열로 바꾼다. JSON 은 Decimal 을 직렬화하지 못한다.

    float 로 바꾸면 값이 미세하게 달라진다. 스냅샷은 판정 근거이므로
    보이는 그대로 남긴다.
    """
    return {k: str(v) for k, v in values.items()}
