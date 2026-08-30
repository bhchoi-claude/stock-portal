# keyword(키워드 사전)와 keyword_mention 테이블 접근 함수

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import psycopg


def keyword_terms(cur: psycopg.Cursor) -> dict[str, int]:
    """표현 -> 대표 keyword_id.

    동의어는 `canonical_id` 가 대표어를 가리킨다. 집계는 대표어로만 센다.
    '글라스기판' 과 '유리기판' 이 따로 세어지면 둘 다 급등으로 보이지 않는다.
    """
    cur.execute(
        "SELECT term, COALESCE(canonical_id, keyword_id) FROM keyword ORDER BY term"
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def insert_keyword_mentions(
    cur: psycopg.Cursor, pairs: Sequence[tuple[int, int]]
) -> int:
    """(message_id, keyword_id) 를 적재한다. 같은 쌍은 무시한다."""
    if not pairs:
        return 0
    cur.executemany(
        "INSERT INTO keyword_mention (message_id, keyword_id) VALUES (%s, %s)"
        " ON CONFLICT DO NOTHING",
        list(pairs),
        returning=False,
    )
    return cur.rowcount


def upsert_keywords(cur: psycopg.Cursor, terms: Sequence[str]) -> dict[str, int]:
    """새 표현을 사전에 넣고 표현 -> 대표 id 를 돌려준다.

    LLM 이 만든 표현은 `is_confirmed = FALSE` 로 들어온다.
    **자동으로 병합하지 않는다.** 동의어 판단은 사람이 화면에서 한다
    (INTERFACES.md 7.2).
    """
    if not terms:
        return {}
    cur.executemany(
        "INSERT INTO keyword (term) VALUES (%s) ON CONFLICT (term) DO NOTHING",
        [(term,) for term in terms],
        returning=False,
    )
    cur.execute(
        "SELECT term, COALESCE(canonical_id, keyword_id) FROM keyword"
        " WHERE term = ANY(%s)",
        (list(terms),),
    )
    return dict(cur.fetchall())


@dataclass(frozen=True)
class Surge:
    """급등 후보 하나. is_new 면 기준선이 없어 비율 대신 건수로 판단한 것이다."""

    keyword_id: int
    term: str
    mention_count: int
    ma7: Decimal | None
    surge_ratio: Decimal | None
    is_new: bool


def aggregate_day(cur: psycopg.Cursor, day: date) -> int:
    """하루치 언급을 집계한다. 달력일(KST) 기준이다.

    동의어는 대표 키워드로 모은다. 병합 뒤에 다시 돌리면 값이 합쳐진다.
    """
    cur.execute(
        """
        INSERT INTO keyword_daily (
            keyword_id, trade_date, mention_count, weighted_count
        )
        SELECT COALESCE(k.canonical_id, k.keyword_id),
               (r.published_at AT TIME ZONE 'Asia/Seoul')::date,
               COUNT(*), SUM(s.weight)
        FROM keyword_mention m
        JOIN raw_message r USING (message_id)
        JOIN keyword k USING (keyword_id)
        JOIN source s ON s.source_id = r.source_id
        WHERE (r.published_at AT TIME ZONE 'Asia/Seoul')::date = %s
        GROUP BY 1, 2
        ON CONFLICT (keyword_id, trade_date) DO UPDATE SET
            mention_count  = EXCLUDED.mention_count,
            weighted_count = EXCLUDED.weighted_count
        """,
        (day,),
    )
    return cur.rowcount


def refresh_surge(cur: psycopg.Cursor, day: date) -> int:
    """기준선과 급등도를 다시 계산한다.

    기준선은 **당일을 뺀 직전 7일**의 하루 평균이다. 당일을 넣으면 오늘의
    급등이 제 기준선을 스스로 끌어올려 신호가 약해진다.

    **언급이 없던 날도 0회로 센다.** RANGE 로 날짜 구간을 잡는 이유다.
    행이 있는 날만 평균 내면 어쩌다 한 번 나오는 표현의 기준선이 1.0 이 되어
    급등이 묻힌다.
    """
    cur.execute(
        """
        WITH base AS (
            SELECT keyword_id, trade_date,
                   COALESCE(SUM(mention_count) OVER (
                       PARTITION BY keyword_id ORDER BY trade_date
                       RANGE BETWEEN INTERVAL '7 days' PRECEDING
                                 AND INTERVAL '1 day' PRECEDING
                   ), 0) / 7.0 AS ma7
            FROM keyword_daily
        )
        UPDATE keyword_daily d
        SET ma7 = base.ma7,
            surge_ratio = CASE
                WHEN base.ma7 > 0 THEN d.mention_count / base.ma7
            END
        FROM base
        WHERE d.keyword_id = base.keyword_id
          AND d.trade_date = base.trade_date
          AND d.trade_date = %s
        """,
        (day,),
    )
    return cur.rowcount


def surging(
    cur: psycopg.Cursor,
    day: date,
    *,
    min_ratio: Decimal,
    min_baseline: Decimal,
    new_min_count: int,
) -> list[Surge]:
    """급등 후보. 기준선이 있는 것과 처음 나온 것을 함께 본다.

    처음 나온 표현은 비율을 낼 수 없다. 분모가 0 이면 무한대다.
    그렇다고 버리면 **가장 강한 신호를 버리는 셈**이라 건수로 따로 잡는다.
    """
    cur.execute(
        """
        SELECT d.keyword_id, k.term, d.mention_count, d.ma7, d.surge_ratio,
               d.ma7 = 0 AS is_new
        FROM keyword_daily d
        JOIN keyword k ON k.keyword_id = d.keyword_id
        WHERE d.trade_date = %s
          AND (
            (d.ma7 >= %s AND d.surge_ratio >= %s)
            OR (d.ma7 = 0 AND d.mention_count >= %s)
          )
        ORDER BY d.surge_ratio DESC NULLS LAST, d.mention_count DESC
        """,
        (day, min_baseline, min_ratio, new_min_count),
    )
    return [Surge(*row) for row in cur.fetchall()]


def alerted_terms(cur: psycopg.Cursor, process_name: str, day: date) -> set[str]:
    """그날 이미 알린 표현. 10분마다 도는 배치가 같은 것을 되풀이하지 않게 한다.

    별도 표를 두지 않고 `event_log` 를 본다. 알림은 어차피 이력으로 남겨야 한다.
    """
    cur.execute(
        "SELECT detail->>'term' FROM event_log"
        " WHERE process_name = %s AND message = '급등 키워드'"
        "   AND detail->>'date' = %s",
        (process_name, str(day)),
    )
    return {row[0] for row in cur.fetchall() if row[0]}


@dataclass(frozen=True)
class DailyKeyword:
    """하루치 키워드 한 줄. 화면과 API 가 함께 쓴다."""

    keyword_id: int
    term: str
    mention_count: int
    weighted_count: Decimal | None
    ma7: Decimal | None
    surge_ratio: Decimal | None
    is_confirmed: bool


def daily_ranked(cur: psycopg.Cursor, day: date, limit: int) -> list[DailyKeyword]:
    """그날 많이 나온 순서. 급등 여부는 화면 쪽에서 임계값과 견준다."""
    cur.execute(
        """
        SELECT k.keyword_id, k.term, d.mention_count, d.weighted_count,
               d.ma7, d.surge_ratio, k.is_confirmed
        FROM keyword_daily d
        JOIN keyword k ON k.keyword_id = d.keyword_id
        WHERE d.trade_date = %s
        ORDER BY d.mention_count DESC, k.term
        LIMIT %s
        """,
        (day, limit),
    )
    return [DailyKeyword(*row) for row in cur.fetchall()]


def merge_keywords(cur: psycopg.Cursor, into: int, from_ids: Sequence[int]) -> int:
    """동의어를 대표어로 묶는다. 사용자가 화면에서 하는 유일한 쓰기 동작이다.

    대상이 이미 다른 대표어를 가리키면 그쪽으로 따라간다. 사슬을 만들지 않는다.

    묶인 쪽의 집계 행은 지운다. 다시 집계하면 대표어 아래로 합쳐지는데,
    지우지 않으면 옛 숫자가 화면에 남는다.
    """
    cur.execute(
        "SELECT COALESCE(canonical_id, keyword_id) FROM keyword WHERE keyword_id = %s",
        (into,),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"없는 키워드입니다: {into}")
    target = row[0]

    merged = [keyword_id for keyword_id in from_ids if keyword_id != target]
    if not merged:
        return 0

    cur.execute(
        "UPDATE keyword SET canonical_id = %s WHERE keyword_id = ANY(%s)",
        (target, merged),
    )
    changed = cur.rowcount
    # 대표어를 확인된 것으로 본다. 사람이 직접 고른 것이기 때문이다
    cur.execute(
        "UPDATE keyword SET is_confirmed = TRUE WHERE keyword_id = %s", (target,)
    )
    cur.execute("DELETE FROM keyword_daily WHERE keyword_id = ANY(%s)", (merged,))
    return changed
