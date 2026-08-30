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
- 종목명과 회사명은 뽑지 않는다. 약칭도 마찬가지다 (NV, 삼전, 닉스)
- 시황 표현(급등, 상승, 하락, 특징주, 강세)은 뽑지 않는다
- 좁은 표현을 우선한다. '반도체' 보다 'HBM', '유리기판' 처럼 구체적인 것을 쓴다
- 원문에 나온 표현 그대로 쓴다. 새로 만들지 않는다
- 글마다 0개에서 5개. 없으면 빈 배열
- `index` 는 글 앞에 붙은 번호다. **모든 번호를 빠짐없이 답한다.**
  키워드가 없는 글도 빈 배열로 답한다. 건너뛰지 않는다"""


def schema_for(count: int) -> dict[str, Any]:
    """글 번호를 함께 받는다.

    번호가 없으면 개수가 어긋났을 때 어느 글의 결과인지 알 수 없어 배치를
    통째로 버려야 한다. 20건 중 19건이 멀쩡해도 다 버렸다 (2026-08-30).

    개수도 함께 못박는다. 지켜지면 누락 자체가 없어진다.
    """
    return {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "minItems": count,
                "maxItems": count,
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "keywords": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["index", "keywords"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["results"],
        "additionalProperties": False,
    }


# 사전에 넣기에 너무 긴 표현은 문장이지 키워드가 아니다
MAX_TERM_LENGTH = 30


@dataclass(frozen=True)
class Extraction:
    """한 배치의 결과. 글 번호(1부터) -> 키워드."""

    keywords: dict[int, list[str]]
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

        답이 온 글만 돌려준다. 빠진 글은 표시하지 않아 다음 주기에 다시 간다.
        모자란다고 배치를 통째로 버리지 않는다.
        """
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_output_tokens,
            system=SYSTEM,
            messages=[{"role": "user", "content": self._prompt(texts)}],
            # 형식을 강제한다. 전문이나 코드펜스가 섞일 자리를 없앤다
            output_config={
                "format": {"type": "json_schema", "schema": schema_for(len(texts))}
            },
        )

        text = next(block.text for block in response.content if block.type == "text")
        found: dict[int, list[str]] = {}
        for item in json.loads(text)["results"]:
            index = item.get("index")
            # 범위 밖 번호는 버린다. 어느 글인지 알 수 없다
            if isinstance(index, int) and 1 <= index <= len(texts):
                found[index] = clean_terms(item.get("keywords") or [])

        return Extraction(
            keywords=found,
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
