# 원문에서 종목과 키워드를 뽑는 규칙 분석기. LLM 을 쓰지 않는다 (PROJECT.md 9장)

from __future__ import annotations

import re
from collections.abc import Sequence

from common.db.messages import StoredMessage

# 링크는 본문이 아니다. 길이를 재기 전에 먼저 지운다.
# 지우지 않으면 링크만 있는 25자 글이 '알찬 글' 로 보인다
URL = re.compile(r"https?://\S+")

# 종목코드는 여섯 자리다. 앞뒤에 숫자가 더 붙으면 다른 것이다.
# 링크 안의 기사 ID(20260828001046)를 종목코드로 읽지 않으려는 것이다
CODE = re.compile(r"(?<![0-9])[0-9]{6}(?![0-9])")


class NewsAnalyzer:
    """규칙 기반 분석. 사전이 커질수록 LLM 호출이 줄어든다 (INTERFACES.md 7장).

    사전을 생성자로 받는다. DB 를 모르는 채로 두어야 시험할 수 있다.
    """

    def __init__(
        self,
        *,
        stocks: dict[str, str],
        codes: dict[str, str],
        keywords: dict[str, int],
        min_length: int,
        footers: Sequence[str],
        ad_patterns: Sequence[str],
    ) -> None:
        self.stocks = stocks
        self.codes = codes
        self.keywords = keywords
        self.min_length = min_length
        self.footers = footers
        self.ad_patterns = ad_patterns

    def normalize(self, content: str) -> str:
        """링크와 채널 홍보 꼬리를 걷어낸다.

        꼬리표는 모든 글 끝에 붙어 있어 놔두면 매일 급등 키워드가 된다.
        """
        text = URL.sub(" ", content)
        for footer in self.footers:
            text = text.replace(footer, " ")
        return " ".join(text.split())

    def is_noise(self, content: str) -> bool:
        """분석할 것이 없는 글인지. 링크를 지운 뒤의 길이로 잰다."""
        text = self.normalize(content)
        if len(text) < self.min_length:
            return True
        return any(pattern in text for pattern in self.ad_patterns)

    def filter(self, records: Sequence[StoredMessage]) -> list[StoredMessage]:
        """규칙 필터를 통과한 것만 돌려준다."""
        return [record for record in records if not self.is_noise(record.content)]

    def match_stocks(self, content: str) -> list[str]:
        """상장사 명단 사전 매칭. LLM 미사용.

        종목코드도 함께 본다. 링크를 지운 뒤라서 기사 ID 와 섞이지 않는다.
        """
        found = {stock_id for name, stock_id in self.stocks.items() if name in content}
        for code in CODE.findall(content):
            stock_id = self.codes.get(code)
            if stock_id:
                found.add(stock_id)
        return sorted(found)

    def match_keywords(self, content: str) -> tuple[list[int], str]:
        """키워드 사전 매칭. (대표 keyword_id, 미매칭 잔여 텍스트).

        잡힌 표현은 잔여 텍스트에서 지운다. 남은 부분만 LLM 이 본다.
        """
        found: set[int] = set()
        rest = content
        for term, keyword_id in self.keywords.items():
            if term in rest:
                found.add(keyword_id)
                rest = rest.replace(term, " ")
        return sorted(found), " ".join(rest.split())
