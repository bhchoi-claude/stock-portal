# 지표값으로 시장국면을 판정한다. 규칙은 config/regime_rules.yaml 에 있다

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from common.config import load_config
from common.db.conn import connect, transaction
from common.db.events import log_event
from common.db.regime import (
    is_override,
    previous_regime,
    upsert_market_regime,
    value_as_of,
)
from common.notify.base import Notifier
from common.types import Regime

logger = logging.getLogger(__name__)


class InsufficientData(RuntimeError):
    """쓸 수 있는 지표가 하나도 없다. 판정하지 않는다.

    값이 없는데 중립으로 적으면 '판단했다' 는 거짓 기록이 남는다.
    """


@dataclass(frozen=True)
class RegimeResult:
    regime: Regime
    score: Decimal
    layer_scores: dict[str, Decimal]
    indicators: dict[str, Decimal]  # 판정에 쓴 값 스냅샷
    rule_version: str


def indicator_score(value: Decimal, danger: Decimal, safe: Decimal) -> Decimal:
    """지표값 하나를 -1 ~ +1 로 옮긴다.

    danger 에서 -1, safe 에서 +1 이고 그 사이는 선형이다. 밖은 잘라낸다.
    danger 가 safe 보다 커도 된다. VKOSPI 처럼 값이 클수록 위험한 지표는
    분모가 음수가 되어 방향이 저절로 뒤집힌다.
    """
    if danger == safe:
        raise ValueError("danger 와 safe 가 같으면 방향을 정할 수 없습니다.")
    raw = 2 * (value - danger) / (safe - danger) - 1
    return max(Decimal(-1), min(Decimal(1), raw))


def is_fresh(period_date: date, as_of: date, max_age_days: int | None) -> bool:
    """묵은 값인지 본다. 죽은 소스의 마지막 값이 계속 쓰이면 안 된다."""
    if max_age_days is None:
        return True
    return period_date >= as_of - timedelta(days=max_age_days)


def weighted_mean(scored: list[tuple[Decimal, Decimal]]) -> Decimal:
    """(가중치, 점수) 목록의 가중평균.

    빠진 항목은 애초에 목록에 없다. 남은 가중치로만 나누므로 정규화가 된다.
    빠진 지표를 0 으로 넣는 것과 다르다 (INTERFACES.md 8.2).
    """
    total = sum(w for w, _ in scored)
    return sum(w * s for w, s in scored) / total


def evaluate(
    rules: dict[str, Any], values: dict[str, tuple[date, Decimal]], as_of: date
) -> RegimeResult:
    """지표값 스냅샷으로 국면을 판정한다.

    `values` 는 지표코드 -> (기준일, 값) 이다. 없는 지표는 키가 없다.
    """
    layer_scores: dict[str, Decimal] = {}
    used: dict[str, Decimal] = {}
    layer_pairs: list[tuple[Decimal, Decimal]] = []

    for layer, spec in rules["layers"].items():
        scored: list[tuple[Decimal, Decimal]] = []

        for item in spec["indicators"]:
            code = item["code"]
            found = values.get(code)
            if found is None:
                continue
            period_date, value = found
            if not is_fresh(period_date, as_of, item.get("max_age_days")):
                logger.info("%s 값이 %s 로 묵어 판정에서 뺍니다", code, period_date)
                continue

            thresholds = item["thresholds"]
            score = indicator_score(
                value,
                Decimal(str(thresholds["danger"])),
                Decimal(str(thresholds["safe"])),
            )
            scored.append((Decimal(str(item["weight"])), score))
            used[code] = value

        # 계층 안의 지표가 전부 없으면 그 계층도 뺀다
        if not scored:
            continue
        layer_scores[layer] = weighted_mean(scored)
        layer_pairs.append((Decimal(str(spec["weight"])), layer_scores[layer]))

    if not layer_pairs:
        raise InsufficientData(f"{as_of} 에 쓸 수 있는 지표가 없습니다.")

    score = weighted_mean(layer_pairs)
    output = rules["output"]
    if score < Decimal(str(output["danger_below"])):
        regime = Regime.DANGER
    elif score > Decimal(str(output["safe_above"])):
        regime = Regime.SAFE
    else:
        regime = Regime.NEUTRAL

    return RegimeResult(
        regime=regime,
        score=score,
        layer_scores=layer_scores,
        indicators=used,
        rule_version=rules["version"],
    )


def collect_values(
    cur, rules: dict[str, Any], as_of: date
) -> dict[str, tuple[date, Decimal]]:
    """규칙에 나오는 지표의 값을 모은다. 없는 것은 키가 없다."""
    values: dict[str, tuple[date, Decimal]] = {}
    for spec in rules["layers"].values():
        for item in spec["indicators"]:
            found = value_as_of(cur, item["code"], item.get("metric", "value"), as_of)
            if found is not None:
                values[item["code"]] = found
    return values


def main(argv: list[str], notifier: Notifier | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    rules = load_config("regime_rules")

    with connect() as conn, transaction(conn) as cur:
        if len(argv) > 1:
            as_of = date.fromisoformat(argv[1])
        else:
            # 판정은 거래일 기준이다. 오늘이 휴장일일 수 있다
            cur.execute("SELECT MAX(trade_date) FROM price_daily")
            as_of = cur.fetchone()[0]
        if as_of is None:
            print("일봉이 없어 판정 기준일을 정할 수 없습니다.")
            return 1

        if is_override(cur, as_of):
            print(f"{as_of} 는 수동 판정으로 고정돼 있습니다. 건드리지 않습니다.")
            return 0

        values = collect_values(cur, rules, as_of)
        try:
            result = evaluate(rules, values, as_of)
        except InsufficientData as exc:
            # 값이 없는데 중립으로 적으면 거짓 기록이 남는다
            logger.warning("%s", exc)
            log_event(
                cur,
                "regime",
                "WARN",
                "지표가 없어 판정하지 않음",
                category="regime",
                detail={"as_of": str(as_of)},
            )
            print(f"{as_of} 판정 보류. 쓸 수 있는 지표가 없습니다.")
            return 0

        before = previous_regime(cur, as_of)
        upsert_market_regime(
            cur,
            as_of,
            result.regime.value,
            result.score,
            result.layer_scores,
            result.indicators,
            result.rule_version,
        )
        log_event(
            cur,
            "regime",
            "INFO",
            "국면 판정",
            category="regime",
            detail={
                "as_of": str(as_of),
                "regime": result.regime.value,
                "score": str(result.score),
                "previous": before,
                "indicators": sorted(result.indicators),
            },
        )

    changed = before is not None and before != result.regime.value
    if changed and notifier is not None:
        # 같은 국면이 유지되면 무음이다 (PROJECT.md 10장)
        notifier.send(
            "WARN",
            "시장국면 전환",
            f"{as_of}\n{before} -> {result.regime.value}\n점수 {result.score:.3f}",
        )

    arrow = f" ({before} -> {result.regime.value})" if changed else ""
    print(
        f"{as_of} 국면 {result.regime.value}"
        f" 점수 {result.score:.3f}{arrow}."
        f" 지표 {len(result.indicators)}종."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
