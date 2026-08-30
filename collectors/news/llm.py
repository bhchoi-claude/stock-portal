# 사전에 없는 표현을 LLM 으로 뽑는다. 정보수집에서만 쓴다 (PROJECT.md 9장)

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

# 키워드는 잘게 뽑는다. '반도체 120회' 는 정보가 아니고
# '유리기판 평소 4회 -> 오늘 30회' 가 정보다 (SCHEMA.md keyword_daily)
SYSTEM = """너는 한국 주식 시장 글에서 키워드를 뽑는다.

규칙
- 테마·산업·기술 키워드만 뽑는다
- 종목명과 회사명은 뽑지 않는다. 그쪽은 따로 처리한다
- 시황 표현(급등, 상승, 하락, 특징주, 강세)은 뽑지 않는다
- 좁은 표현을 우선한다. '반도체' 보다 'HBM', '유리기판' 처럼 구체적인 것을 쓴다
- 원문에 나온 표현 그대로 쓴다. 새로 만들지 않는다
- 글마다 0개에서 5개. 없으면 빈 배열
- 입력 순서와 출력 순서를 반드시 맞춘다"""

SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {"type": "array", "items": {"type": "string"}},
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}

# 사전에 넣기에 너무 긴 표현은 문장이지 키워드가 아니다
MAX_TERM_LENGTH = 30


@dataclass(frozen=True)
class Extraction:
    """한 배치의 결과. 순서가 입력과 같다."""

    keywords: list[list[str]]
    input_tokens: int
    output_tokens: int


class KeywordExtractor:
    """배치로만 호출한다. 단건 호출은 하지 않는다 (INTERFACES.md 7.1)."""

    def __init__(
        self, client: Any, model: str, max_output_tokens: int, max_chars: int
    ) -> None:
        self.client = client
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.max_chars = max_chars

    def extract(self, texts: list[str]) -> Extraction:
        """미매칭 텍스트 여러 건에서 키워드를 뽑는다.

        출력 개수가 입력과 다르면 어느 글의 키워드인지 알 수 없다.
        그때는 배치 전체를 버리고 다음 주기에 다시 한다.
        """
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_output_tokens,
            system=SYSTEM,
            messages=[{"role": "user", "content": self._prompt(texts)}],
            # 형식을 강제한다. 전문이나 코드펜스가 섞일 자리를 없앤다
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )

        text = next(block.text for block in response.content if block.type == "text")
        results = json.loads(text)["results"]
        if len(results) != len(texts):
            raise ValueError(f"입력 {len(texts)}건에 출력 {len(results)}건입니다.")

        return Extraction(
            keywords=[clean_terms(terms) for terms in results],
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    def _prompt(self, texts: list[str]) -> str:
        numbered = [
            f"[{index}] {text[: self.max_chars]}" for index, text in enumerate(texts, 1)
        ]
        return "\n\n".join(numbered)


def clean_terms(terms: list[str]) -> list[str]:
    """공백을 다듬고 문장처럼 긴 것을 버린다. 순서와 중복을 정리한다."""
    seen = []
    for term in terms:
        cleaned = " ".join(str(term).split())
        if cleaned and len(cleaned) <= MAX_TERM_LENGTH and cleaned not in seen:
            seen.append(cleaned)
    return seen


def cost_of(input_tokens: int, output_tokens: int, params: dict[str, Any]) -> Decimal:
    """토큰 수를 금액으로. 단가는 설정에서 읽는다."""
    million = Decimal(1_000_000)
    return Decimal(input_tokens) / million * Decimal(
        str(params["price_input_per_mtok"])
    ) + Decimal(output_tokens) / million * Decimal(str(params["price_output_per_mtok"]))
