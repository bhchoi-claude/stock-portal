# 주식수 증가 이벤트를 DART 발행형태로 분류하는 CLI

from __future__ import annotations

import logging
import sys
from datetime import date

from common.broker.errors import BrokerError
from common.config import load_config
from common.db.actions import unclassified_increases, update_action_type
from common.db.conn import connect, transaction
from common.db.events import log_event

from .dart import corp_codes, share_events

logger = logging.getLogger(__name__)

# 발행형태 -> (action_type, 가격 조정 대상 여부)
#
# 무상증자와 주식분할은 대가 없이 주식이 늘어 가격이 조정된다.
# 유상증자·전환권행사·신주인수권행사·스톡옵션은 대가를 받고 발행하므로
# 기존 주주의 주식 가치가 기계적으로 나뉘지 않는다. 조정 대상이 아니다.
STYLES = {
    "무상증자": ("bonus", True),
    "주식분할": ("split", True),
    "유상증자": ("rights", False),
    "전환권행사": ("rights", False),
    "신주인수권행사": ("rights", False),
    "주식매수선택권행사": ("rights", False),
    "주식매수선택권행사(자기주식교부)": ("rights", False),
}


def classify_style(style: str) -> tuple[str, bool] | None:
    """발행형태 문자열을 (action_type, adjusts_price) 로 바꾼다.

    `유상증자(일반공모)` 처럼 괄호가 붙어 오므로 앞부분으로 맞춘다.
    """
    for prefix, result in STYLES.items():
        if style.startswith(prefix):
            return result
    return None


def to_int(value: str) -> int | None:
    """`1,948,811` 같은 콤마 있는 수를 읽는다."""
    try:
        return int(value.replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def parse_dart_date(value: str) -> date | None:
    """`2025.11.06` 을 날짜로. 형식이 다르면 None."""
    try:
        year, month, day = value.split(".")
        return date(int(year), int(month), int(day))
    except (ValueError, AttributeError):
        return None


def match_event(
    events: list[dict[str, str]], delta: int, effective: date, window_days: int
) -> dict[str, str] | None:
    """발행 수량이 같고 날짜가 가까운 DART 이벤트를 고른다.

    **수량이 주된 키다.** DART 는 발행일을, 우리는 상장주식수 변경일을 쓰므로
    날짜가 며칠씩 어긋난다. 수량이 맞고 창 안에 있는 것 중 가장 가까운 것을 쓴다.
    """
    candidates = []
    for event in events:
        if to_int(event.get("isu_dcrs_qy", "")) != delta:
            continue
        issued = parse_dart_date(event.get("isu_dcrs_de", ""))
        if issued is None or abs((effective - issued).days) > window_days:
            continue
        candidates.append((abs((effective - issued).days), event))

    if not candidates:
        return None
    return min(candidates, key=lambda pair: pair[0])[1]


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    params = load_config("collect")["corporate_action"]
    window = params["dart_match_window_days"]

    with connect() as conn, transaction(conn) as cur:
        pending = unclassified_increases(cur)
    if not pending:
        print("분류할 증가 이벤트가 없습니다.")
        return 0

    logger.info("증가 이벤트 %d건", len(pending))

    try:
        codes = corp_codes()
    except BrokerError:
        logger.exception("DART 고유번호 목록을 받지 못했습니다")
        return 1
    logger.info("DART 고유번호 매핑 %d건", len(codes))

    classified = 0
    unmatched: list[str] = []
    cache: dict[tuple[str, int], list[dict[str, str]]] = {}

    for action_id, stock_id, effective, delta in pending:
        code = stock_id.rpartition(":")[2]
        corp = codes.get(code)
        if corp is None:
            # 폐지 종목은 DART 목록에서 빠진다
            unmatched.append(f"{stock_id} (고유번호 없음)")
            continue

        # 발행일이 전년일 수 있어 두 해를 본다
        events: list[dict[str, str]] = []
        for year in (effective.year, effective.year - 1):
            key = (corp, year)
            if key not in cache:
                try:
                    cache[key] = share_events(corp, year)
                except BrokerError:
                    logger.exception("%s %d 조회 실패", stock_id, year)
                    cache[key] = []
            events += cache[key]

        found = match_event(events, delta, effective, window)
        if found is None:
            unmatched.append(f"{stock_id} {effective} delta={delta:,}")
            continue

        style = found.get("isu_dcrs_stle", "")
        mapped = classify_style(style)
        if mapped is None:
            unmatched.append(f"{stock_id} {effective} 모르는 형태 {style}")
            continue

        action_type, adjusts = mapped
        with connect() as conn, transaction(conn) as cur:
            update_action_type(cur, action_id, action_type, adjusts, style)
        classified += 1
        logger.info(
            "%s %s %s -> %s (조정 %s)",
            stock_id,
            effective,
            style,
            action_type,
            "O" if adjusts else "X",
        )

    with connect() as conn, transaction(conn) as cur:
        log_event(
            cur,
            "classify_increase",
            "WARN" if unmatched else "INFO",
            "증가 이벤트 분류",
            category="collect",
            detail={"classified": classified, "unmatched": unmatched},
        )

    if unmatched:
        logger.warning("못 맞춘 것 %d건: %s", len(unmatched), unmatched[:10])

    print(f"{len(pending)}건 중 {classified}건 분류, {len(unmatched)}건 미매칭.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
