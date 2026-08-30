# keyword(키워드 사전)와 keyword_mention 테이블 접근 함수

from __future__ import annotations

from collections.abc import Sequence

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
