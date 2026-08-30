# 적재된 원문을 규칙으로 분석하는 배치. 사전 매칭까지만 하고 LLM 은 쓰지 않는다

from __future__ import annotations

import logging
import sys
from typing import Any

from common.config import load_config
from common.db import master
from common.db.conn import connect, transaction
from common.db.events import log_event
from common.db.heartbeat import run_with_heartbeat
from common.db.keywords import insert_keyword_mentions, keyword_terms
from common.db.messages import (
    insert_stock_mentions,
    mark_analyzed,
    unanalyzed,
)

from .analyzer import NewsAnalyzer

logger = logging.getLogger(__name__)

PROCESS = "news_analyze"

# 사전 매칭만으로 끝냈다는 표시. LLM 이 붙으면 'llm' 이 된다 (SCHEMA.md 3장)
METHOD = "dict"


def build_analyzer(cur, params: dict[str, Any]) -> NewsAnalyzer:
    entries = master.stock_dictionary(
        cur, params["stock"]["min_name_length"], params["stock"]["excludes"]
    )
    return NewsAnalyzer(
        stocks={name: stock_id for name, stock_id, _ in entries},
        codes={code: stock_id for _, stock_id, code in entries},
        keywords=keyword_terms(cur),
        min_length=params["min_length"],
        footers=params["footers"],
        ad_patterns=params["ad_patterns"],
    )


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    params = load_config("collect")["news"]

    with connect() as conn, transaction(conn) as cur:
        analyzer = build_analyzer(cur, params)
        messages = unanalyzed(cur, params["batch_size"])

    if not messages:
        print("분석할 원문이 없습니다.")
        return 0

    kept = analyzer.filter(messages)
    kept_ids = {message.message_id for message in kept}
    noise = [m.message_id for m in messages if m.message_id not in kept_ids]

    stock_pairs: list[tuple[int, str]] = []
    keyword_pairs: list[tuple[int, int]] = []
    unmatched = 0

    for message in kept:
        text = analyzer.normalize(message.content)
        stock_pairs += [(message.message_id, s) for s in analyzer.match_stocks(text)]
        keyword_ids, rest = analyzer.match_keywords(text)
        keyword_pairs += [(message.message_id, k) for k in keyword_ids]
        if rest:
            # 사전에 없는 표현이 남았다. 3단계에서 LLM 이 볼 부분이다
            unmatched += 1

    with connect() as conn, transaction(conn) as cur:
        insert_stock_mentions(cur, stock_pairs)
        insert_keyword_mentions(cur, keyword_pairs)
        mark_analyzed(cur, noise, METHOD, filtered=True)
        mark_analyzed(cur, sorted(kept_ids), METHOD)
        log_event(
            cur,
            PROCESS,
            "INFO",
            "규칙 분석",
            category="collect",
            detail={
                "messages": len(messages),
                "filtered": len(noise),
                "stocks": len(stock_pairs),
                "keywords": len(keyword_pairs),
                "unmatched": unmatched,
            },
        )

    print(
        f"원문 {len(messages)}건 중 {len(noise)}건 제외."
        f" 종목 {len(stock_pairs)}건, 키워드 {len(keyword_pairs)}건 매칭."
        f" 잔여 텍스트가 남은 글 {unmatched}건."
    )
    return 0


if __name__ == "__main__":
    sys.exit(run_with_heartbeat(PROCESS, main, sys.argv))
