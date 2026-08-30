# api_usage(호출량·비용) 테이블 접근 함수. 일일 상한 판단에 쓴다

from __future__ import annotations

from datetime import date
from decimal import Decimal

import psycopg


def today_cost(cur: psycopg.Cursor, provider: str, usage_date: date) -> Decimal:
    """그날 그 제공자에 쓴 금액. 기록이 없으면 0."""
    cur.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM api_usage"
        " WHERE usage_date = %s AND provider = %s",
        (usage_date, provider),
    )
    return cur.fetchone()[0]


def record_usage(
    cur: psycopg.Cursor,
    usage_date: date,
    provider: str,
    endpoint: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cost_usd: Decimal,
) -> None:
    """호출 한 건을 누적한다. 같은 날 같은 엔드포인트면 더한다."""
    cur.execute(
        """
        INSERT INTO api_usage (
            usage_date, provider, endpoint,
            call_count, input_tokens, output_tokens, cost_usd
        )
        VALUES (%s, %s, %s, 1, %s, %s, %s)
        ON CONFLICT (usage_date, provider, endpoint) DO UPDATE SET
            call_count    = api_usage.call_count + 1,
            input_tokens  = api_usage.input_tokens + EXCLUDED.input_tokens,
            output_tokens = api_usage.output_tokens + EXCLUDED.output_tokens,
            cost_usd      = api_usage.cost_usd + EXCLUDED.cost_usd
        """,
        (usage_date, provider, endpoint, input_tokens, output_tokens, cost_usd),
    )
