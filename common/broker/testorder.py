# 장중 시험주문을 예약 실행으로 대신하는 일회용 도구 (checklist 1단계 마감용)

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ..db.conn import connect, transaction
from ..db.prices import raw_close
from .errors import BrokerError, TransientError
from .kiwoom import (
    ACCOUNT_PATH,
    BUY_API,
    CANCEL_API,
    FILLED_API,
    INFO_PATH,
    ORDER_PATH,
    QUOTE_API,
    UNFILLED_API,
    KiwoomBroker,
    strip_sign,
)
from .probe import _mask

log = logging.getLogger(__name__)

# 사람이 아침에 못 앉아 있을 때 대신 돌린다. 1단계가 닫히면 지운다.
#
# 확인하려는 것 셋 (checklist "장중 시험주문 절차")
#   1. submit_order 성공 응답의 주문번호 필드
#   2. 미체결 상태의 ord_stt 값
#   3. 취소된 주문이 어느 목록에 남는가

STOCK_ID = "KRX:005930"
CODE = "005930"
ACCOUNT_ID = "paper"

# 하한가 지정가 1주. 정상 접수되면서 체결되지 않는다.
# **체결돼도 상관없다** — 그것도 실측이라 기록하고 넘어간다
QUANTITY = "1"
LIMIT_ORDER = "0"


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    marker = pathlib.Path(args.out).with_suffix(".done")
    if marker.exists() and not args.force:
        # 타이머가 두 번 발화해도 주문이 두 번 나가지 않는다
        log.info("이미 돌았습니다: %s", marker)
        return 0

    broker = KiwoomBroker(is_paper=True)
    record: dict[str, Any] = {"started_at": _now(), "steps": []}

    try:
        code = _run(broker, record, args)
    except Exception as exc:  # 무엇이든 기록은 남긴다
        log.exception("시험주문이 중단됐습니다")
        record["fatal"] = f"{type(exc).__name__}: {exc}"
        code = 1

    record["finished_at"] = _now()
    _save(pathlib.Path(args.out), record)
    marker.write_text(record["finished_at"], encoding="utf-8")
    return code


def _run(broker: KiwoomBroker, record: dict[str, Any], args) -> int:
    """6단계를 순서대로. 주문이 나가면 **반드시 취소까지 간다.**"""
    price = _wait_for_price(broker, record, args)
    if price is None:
        log.error("기준가가 넘어가지 않아 주문하지 않았습니다")
        return 1

    order_no = _place(broker, record, price, args)
    if order_no is None:
        return 1

    _step(record, "미체결(주문 직후)", lambda: _unfilled(broker))
    _step(record, "get_order_status(주문 직후)", lambda: _status(broker, order_no))

    _step(record, "취소", lambda: _cancel(broker, order_no))

    # 취소가 반영될 틈을 준다. 즉시 조회하면 아직 남아 있을 수 있다
    time.sleep(args.settle_sec)

    _step(record, "미체결(취소 뒤)", lambda: _unfilled(broker))
    _step(record, "체결목록(취소 뒤)", lambda: _filled(broker))
    _step(record, "get_order_status(취소 뒤)", lambda: _status(broker, order_no))
    return 0


# --- 1. 하한가 ----------------------------------------------------------------


def _wait_for_price(broker: KiwoomBroker, record: dict[str, Any], args) -> str | None:
    """오늘 하한가를 읽는다. **기준가가 넘어갔는지 확인하고 쓴다.**

    장이 열리기 전에 읽으면 지난 장의 값이 그대로 나온다 (2026-08-31 실측).
    그 하한가로 주문하면 범위를 벗어나 거부되고, 아무것도 못 잰다.

    기준가는 전 거래일 종가여야 한다. `price_daily` 의 마지막 종가와
    대조한다. 다를 때는 잠시 기다렸다 다시 본다.
    """
    expected = _last_close()
    log.info("전 거래일 종가 %s 를 기준가로 기대합니다", expected)

    for attempt in range(1, args.price_attempts + 1):
        data = broker._call_once(QUOTE_API, INFO_PATH, {"stk_cd": CODE})
        base = strip_sign(data["base_pric"])
        low = strip_sign(data["lst_pric"])
        record["steps"].append(
            {
                "step": f"시세 {attempt}회",
                "at": _now(),
                "base_pric": str(base),
                "lst_pric": str(low),
                "upl_pric": str(strip_sign(data["upl_pric"])),
                "cur_prc": str(strip_sign(data["cur_prc"])),
                "expected_base": str(expected) if expected else None,
            }
        )

        if expected is None or base == expected:
            log.info("기준가 %s, 하한가 %s", base, low)
            # 부호를 뗀 정수만 보낸다. ord_uv 는 정수만 받는다 (1517 실측)
            return str(int(low))

        log.info(
            "기준가가 아직 %s 입니다 (기대 %s). %d초 뒤 다시 봅니다",
            base,
            expected,
            args.price_wait_sec,
        )
        time.sleep(args.price_wait_sec)

    return None


def _last_close() -> Decimal | None:
    """`price_daily` 의 마지막 종가. 없으면 대조를 건너뛴다."""
    with connect() as conn, transaction(conn) as cur:
        row = raw_close(cur, STOCK_ID, datetime.now(UTC).date())
    return row[1] if row else None


# --- 2. 주문 ------------------------------------------------------------------


def _place(
    broker: KiwoomBroker, record: dict[str, Any], price: str, args
) -> str | None:
    """하한가 지정가 1주. **응답을 못 받으면 즉시 멈춘다.**

    거부(본문 `return_code != 0`)는 최종 상태라 다시 낼 수 있다 — 장이 아직
    안 열렸을 때가 그렇다. 그러나 **응답 자체를 못 받으면 접수 여부를
    모르므로 다시 걸지 않는다** (CLAUDE.md 3).
    """
    body = {
        "dmst_stex_tp": "KRX",
        "stk_cd": CODE,
        "ord_qty": QUANTITY,
        "ord_uv": price,
        "trde_tp": LIMIT_ORDER,
    }

    for attempt in range(1, args.order_attempts + 1):
        entry: dict[str, Any] = {
            "step": f"주문 {attempt}회",
            "at": _now(),
            "body": body,
        }
        try:
            data = broker._call_once(
                BUY_API, ORDER_PATH, body, check_return_code=False
            )
        except TransientError as exc:
            # 접수됐는지 모른다. 재시도가 곧 중복 주문이다
            entry["error"] = f"{type(exc).__name__}: {exc}"
            entry["note"] = "응답 없음. 접수 여부를 확인해야 한다"
            record["steps"].append(entry)
            return None
        except BrokerError as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            record["steps"].append(entry)
            return None

        data.pop("_headers", None)
        entry["response"] = _mask(data)
        entry["keys"] = sorted(data)
        record["steps"].append(entry)

        if data.get("return_code") == 0:
            order_no = data.get("ord_no")
            if order_no is None:
                # 규격 가정이 틀렸다. 어느 키에 들어 있는지 눈으로 본다
                log.error("성공 응답에 ord_no 가 없습니다. 키: %s", sorted(data))
            log.info("주문 접수: %s", order_no)
            return order_no

        log.warning("거부: %s", data.get("return_msg"))
        if attempt < args.order_attempts:
            time.sleep(args.order_wait_sec)

    return None


# --- 3~6. 조회와 취소 ---------------------------------------------------------


def _unfilled(broker: KiwoomBroker) -> dict[str, Any]:
    return broker._call_once(
        UNFILLED_API,
        ACCOUNT_PATH,
        {"all_stk_tp": "0", "trde_tp": "0", "stex_tp": "1"},
    )


def _filled(broker: KiwoomBroker) -> dict[str, Any]:
    return broker._call_once(
        FILLED_API,
        ACCOUNT_PATH,
        {"qry_tp": "0", "sell_tp": "0", "stex_tp": "1"},
    )


def _cancel(broker: KiwoomBroker, order_no: str) -> dict[str, Any]:
    """`cncl_qty` 가 `"0"` 이면 잔량 전부 취소다."""
    return broker._call_once(
        CANCEL_API,
        ORDER_PATH,
        {
            "dmst_stex_tp": "KRX",
            "orig_ord_no": order_no,
            "stk_cd": CODE,
            "cncl_qty": "0",
        },
        check_return_code=False,
    )


def _status(broker: KiwoomBroker, order_no: str) -> dict[str, Any]:
    """**구현한 파서를 그대로 돌린다.** probe 가 아니라 이쪽이 진짜 확인이다."""
    result = broker.get_order_status(ACCOUNT_ID, order_no, "TESTORDER")
    return {
        "status": result.status,
        "filled_qty": result.filled_qty,
        "avg_fill_price": str(result.avg_fill_price)
        if result.avg_fill_price is not None
        else None,
        "broker_order_no": result.broker_order_no,
        "error_message": result.error_message,
    }


# --- 보조 --------------------------------------------------------------------


def _step(record: dict[str, Any], name: str, call) -> None:
    """한 단계를 돌리고 결과를 기록한다. **실패해도 다음으로 넘어간다.**

    취소까지 가는 것이 중요하다. 중간 조회가 하나 실패했다고 멈추면
    주문이 장 마감까지 남는다.
    """
    entry: dict[str, Any] = {"step": name, "at": _now()}
    try:
        data = call()
        if isinstance(data, dict):
            data.pop("_headers", None)
        entry["response"] = _mask(data)
    except Exception as exc:
        log.exception("%s 실패", name)
        entry["error"] = f"{type(exc).__name__}: {exc}"
    record["steps"].append(entry)


def _save(path: pathlib.Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("기록: %s", path)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m common.broker.testorder")
    parser.add_argument(
        "--out",
        default="logs/testorder.json",
        help="응답 원문을 남길 파일. 같은 이름의 .done 이 재실행을 막는다",
    )
    parser.add_argument(
        "--force", action="store_true", help=".done 이 있어도 다시 돌린다"
    )
    parser.add_argument("--price-attempts", type=int, default=20)
    parser.add_argument("--price-wait-sec", type=int, default=60)
    parser.add_argument("--order-attempts", type=int, default=3)
    parser.add_argument("--order-wait-sec", type=int, default=120)
    parser.add_argument("--settle-sec", type=int, default=5)
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
