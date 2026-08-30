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

# 이름 앞에 이 글자가 붙어 있으면 다른 낱말의 일부다.
# 한국어에는 띄어쓰기 경계가 없어서 '이닉스' 가 'SK하이닉스' 안에 걸린다.
# 뒤쪽은 볼 수 없다. '삼성전자가' 의 조사와 구분이 안 되기 때문이다
BOUNDARY = re.compile(r"[0-9A-Za-z가-힣]")


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
        # 긴 이름부터 본다. 'HD현대일렉트릭' 을 먼저 지워야 'HD현대' 가
        # 남지 않는다. 둘 다 실제로 언급된 글에서는 둘 다 잡힌다
        self.stock_names = sorted(stocks, key=len, reverse=True)
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

        잡은 이름은 본문에서 지운다. 긴 이름부터 보므로 짧은 이름이 긴 이름
        안에 걸리지 않는다.
        """
        found = set()
        rest = content
        for name in self.stock_names:
            if _appears(rest, name):
                found.add(self.stocks[name])
                rest = rest.replace(name, " ")

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


def _appears(text: str, name: str) -> bool:
    """낱말 경계를 지키며 찾는다.

    앞에 글자가 붙어 있으면 다른 낱말이다. '펩타이드' 의 '타이드' 를
    종목으로 세지 않으려는 것이다.
    """
    start = 0
    while (index := text.find(name, start)) != -1:
        if index == 0 or not BOUNDARY.match(text[index - 1]):
            return True
        start = index + 1
    return False
