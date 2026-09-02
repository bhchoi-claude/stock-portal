# 장중에만 잴 수 있는 것들을 예약 실행으로 재는 일회용 도구 (Phase 8 실측)

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
from zoneinfo import ZoneInfo

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
SEOUL = ZoneInfo("Asia/Seoul")

# 사람이 장중에 못 앉아 있을 때 대신 돌린다. 실측이 끝나면 유닛째 지운다.
#
# 09-01 에 넷 중 둘을 못 쟀다. 남은 셋만 다시 잰다 (2026-09-02).
#
#   1. 슬리피지 — **개장 직후 시장가**의 체결가와 그날 시가의 차이.
#      동시호가로 재려 했으나 모의투자가 장시작전 주문을 안 받는다(RC4057).
#      백테스트가 '다음 날 시가 체결' 을 가정하므로, 개장 직후에 내면 얼마나
#      벌어지는가가 곧 실전에서 감수할 슬리피지다
#   2. 15:30 취소 — 취소도 주문 API 라 장종료로 막힐 수 있다. 막히면 엔진
#      시간표를 다시 짜야 한다. 09-01 에는 _sleep_until 결함으로 못 쟀다
#   3. 취소된 주문의 행방 — 어느 목록에도 없으면 재시작 복구가 못 찾는다.
#      09-01 에는 잔량이 없어 취소 자체가 거부됐다
#
# 이미 닫힌 것은 다시 재지 않는다 — 주문번호(ord_no), 매도 경로,
# 수수료·세금, 부분체결 수량 판정 (INTERFACES.md 2.4)

MAIN_CODE = "005930"  # 삼성전자. 시장가가 확실히 체결된다
MAIN_STOCK_ID = "KRX:005930"
ACCOUNT_ID = "paper"

MARKET_ORDER = "3"
LIMIT_ORDER = "0"

# 장이 열리는 시각(KST). 이 뒤로는 시세가 오늘 세션 값이다
MARKET_OPEN_HOUR = 9


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    out = pathlib.Path(args.out)
    marker = out.with_suffix(".done")
    if marker.exists() and not args.force:
        # 타이머가 두 번 발화해도 주문이 두 번 나가지 않는다
        log.info("이미 돌았습니다: %s", marker)
        return 0

    broker = KiwoomBroker(is_paper=True)
    record: dict[str, Any] = {"started_at": _now(), "steps": []}
    state: dict[str, Any] = {}

    for at, name, run in _plan(args):
        _sleep_until(at, name, args)
        _step(record, out, name, lambda run=run: run(broker, state, args))

    record["finished_at"] = _now()
    record["state"] = {key: str(value) for key, value in state.items()}
    _save(out, record)
    marker.write_text(record["finished_at"], encoding="utf-8")
    log.info("끝났습니다. %s 를 붙여주세요", out)
    return 0


def _plan(args) -> list[tuple[str, str, Any]]:
    """시각표. **순서가 곧 실험 설계다.**

    각 단계는 실패해도 다음으로 넘어간다. 중간 하나가 깨졌다고 멈추면
    주문이 취소되지 않은 채 남는다.
    """
    return [
        ("08:55", "잔고(개장 전)", _balance),
        # --- 1. 슬리피지: 개장 직후 시장가 ------------------------------------
        # 둘을 낸다. 09:00 정각이 거부되면 09:02 가 받아주고, 둘 다 되면
        # **표본이 둘**이다. 개장 직후는 값이 크게 흔들려 한 번으로는 모른다
        ("09:00", "개장 직후 시장가 매수", _buy_market("open_buy")),
        ("09:02", "개장 2분 뒤 시장가 매수", _buy_market("late_buy")),
        ("09:04", "시가 대조 (슬리피지)", _slippage),
        # --- 3. 취소된 주문은 어디에 남는가 -------------------------------------
        ("09:06", "하한가 지정가 매수", _buy_limit),
        ("09:10", "미체결·상태 조회", _look),
        ("09:15", "취소", _cancel_limit),
        ("09:20", "취소 뒤 조회", _look),
        # --- 2. 15:30 취소가 되는가 --------------------------------------------
        ("15:25", "마감용 하한가 지정가 매수", _buy_closing),
        ("15:31", "취소 시도 15:31", _cancel_closing),
        ("15:45", "취소 시도 15:45", _cancel_closing),
        ("16:10", "취소 시도 16:10", _cancel_closing),
        ("16:12", "마지막 미체결 확인", _unfilled_step),
        ("16:13", "잔고(마감 뒤)", _balance),
    ]


# --- 1. 슬리피지 --------------------------------------------------------------


def _buy_market(key: str):
    """개장 직후 시장가 매수 1주를 내는 단계를 만든다.

    **동시호가가 아니라 개장 직후다.** 모의투자는 장시작전 주문을 받지
    않는다 (`RC4057`, 2026-09-01 실측). 백테스트는 다음 날 시가 체결을
    가정하므로, 개장 직후 체결가가 시가에서 얼마나 벌어지는지가 실전에서
    감수할 슬리피지다.

    `ord_uv` 는 빈 문자열이다. 시장가에 가격 필드가 필요 없는 것은
    2026-08-31 에 확인했지만, 규격에 있는 필드를 빼는 것보다 명시적이다.
    """

    def run(broker: KiwoomBroker, state: dict, args) -> dict[str, Any]:
        body = {
            "dmst_stex_tp": "KRX",
            "stk_cd": MAIN_CODE,
            "ord_qty": "1",
            "ord_uv": "",
            "trde_tp": MARKET_ORDER,
        }
        state[key] = _order(broker, state, BUY_API, body)
        return {"body": body, "ord_no": state[key]}

    return run


def _slippage(broker: KiwoomBroker, state: dict, args) -> dict[str, Any]:
    """체결가와 **그날 시가**를 대조한다. Phase 8 의 핵심 산출물이다.

    백테스트는 다음 날 시가에 `slippage_rate` 를 얹어 체결을 흉내낸다.
    실제로 얼마나 벌어지는지는 재봐야 안다.

    표본 둘을 다 본다. 개장 직후는 값이 크게 흔들려 한 번으로는 모른다.
    """
    quote = broker._call_once(QUOTE_API, INFO_PATH, {"stk_cd": MAIN_CODE})
    filled = _filled(broker)
    open_price = strip_sign(quote["open_pric"])

    result: dict[str, Any] = {
        "open_pric": str(open_price),
        "cur_prc": str(strip_sign(quote["cur_prc"])),
        "samples": {},
    }
    for key in ("open_buy", "late_buy"):
        order_no = state.get(key)
        row = _find(filled, order_no)
        sample: dict[str, Any] = {"ord_no": order_no, "filled_row": row}
        if row and open_price:
            price = strip_sign(row.get("cntr_pric") or row.get("ord_uv") or "0")
            sample["fill_price"] = str(price)
            if price:
                # 매수라 시가보다 비싸게 사면 양수다
                sample["slippage"] = str((price - open_price) / open_price)
        result["samples"][key] = sample
    return result


# --- 미체결 상태 어휘 ----------------------------------------------------------


def _buy_limit(broker: KiwoomBroker, state: dict, args) -> dict[str, Any]:
    """하한가 지정가 1주. 정상 접수되면서 체결되지 않는다."""
    price = _limit_price(broker, args)
    if price is None:
        return {"skipped": "기준가가 넘어가지 않았다"}

    body = _limit_body(MAIN_CODE, "1", price)
    state["limit_buy"] = _order(broker, state, BUY_API, body)
    return {"body": body, "ord_no": state["limit_buy"]}


def _look(broker: KiwoomBroker, state: dict, args) -> dict[str, Any]:
    """미체결 원문과 **구현한 파서**를 나란히 남긴다.

    원문만 보면 파서가 맞는지 모르고, 파서 결과만 보면 왜 틀렸는지 모른다.
    """
    return {
        "unfilled": _mask(_unfilled(broker)),
        "status": _status(broker, state.get("limit_buy")),
    }


def _cancel_limit(broker: KiwoomBroker, state: dict, args) -> dict[str, Any]:
    """하한가 주문을 취소한다. **취소된 주문이 어디에 남는지**가 여기서 나온다.

    09-01 에는 유도 주문이 전량 체결돼 잔량이 없어(`RC4033`) 취소 자체가
    거부됐다. 하한가는 체결되지 않으므로 잔량이 확실히 남는다.
    """
    return _cancel(broker, state.get("limit_buy"), MAIN_CODE)


# --- 2. 15:30 취소 ------------------------------------------------------------


def _buy_closing(broker: KiwoomBroker, state: dict, args) -> dict[str, Any]:
    """마감 직전에 미체결을 하나 만든다. 취소가 언제까지 되는지 재려는 것이다."""
    price = _limit_price(broker, args)
    if price is None:
        return {"skipped": "기준가가 넘어가지 않았다"}

    body = _limit_body(MAIN_CODE, "1", price)
    state["closing_buy"] = _order(broker, state, BUY_API, body)
    return {"body": body, "ord_no": state["closing_buy"]}


def _cancel_closing(broker: KiwoomBroker, state: dict, args) -> dict[str, Any]:
    """이미 취소됐으면 건너뛴다. 세 번 시도해 **되는 시각을 찾는다.**"""
    if state.get("closing_cancelled"):
        return {"skipped": "이미 취소됐다"}

    result = _cancel(broker, state.get("closing_buy"), MAIN_CODE)
    if result.get("return_code") == 0:
        state["closing_cancelled"] = True
    return result


# --- 조회 ---------------------------------------------------------------------


def _balance(broker: KiwoomBroker, state: dict, args) -> dict[str, Any]:
    """예수금과 주문가능금액. **둘이 다르다** — 국내는 D+2 결제다."""
    balance = broker.get_balance(ACCOUNT_ID)
    return {
        "deposit": str(balance.deposit),
        "available": str(balance.available),
        "eval_amount": str(balance.eval_amount),
        "total_asset": str(balance.total_asset),
        "positions": {
            p.stock_id: {"qty": p.quantity, "avg": str(p.avg_price)}
            for p in broker.get_positions(ACCOUNT_ID)
        },
    }


def _unfilled_step(broker: KiwoomBroker, state: dict, args) -> dict[str, Any]:
    return {"unfilled": _mask(_unfilled(broker))}


def _unfilled(broker: KiwoomBroker) -> dict[str, Any]:
    return broker._call_once(
        UNFILLED_API, ACCOUNT_PATH, {"all_stk_tp": "0", "trde_tp": "0", "stex_tp": "1"}
    )


def _filled(broker: KiwoomBroker) -> dict[str, Any]:
    return broker._call_once(
        FILLED_API, ACCOUNT_PATH, {"qry_tp": "0", "sell_tp": "0", "stex_tp": "1"}
    )


def _status(broker: KiwoomBroker, order_no: str | None) -> dict[str, Any] | None:
    """**구현한 파서를 그대로 돌린다.** 원문이 아니라 이쪽이 진짜 확인이다."""
    if not order_no:
        return None
    try:
        result = broker.get_order_status(ACCOUNT_ID, order_no, "EXPERIMENT")
    except BrokerError as exc:
        # 취소된 주문이 목록에서 사라지면 여기로 온다. 그것도 실측이다
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "status": result.status,
        "filled_qty": result.filled_qty,
        "avg_fill_price": str(result.avg_fill_price)
        if result.avg_fill_price is not None
        else None,
        "broker_order_no": result.broker_order_no,
    }


# --- 주문 ---------------------------------------------------------------------


def _order(
    broker: KiwoomBroker, state: dict, api_id: str, body: dict[str, str]
) -> str | None:
    """주문 하나. **응답을 못 받으면 다시 걸지 않는다** (CLAUDE.md 3).

    접수됐는지 모르는 상태에서 재시도하면 그대로 중복 주문이다. 거부는
    최종 상태라 기록만 하고 넘어간다.
    """
    try:
        data = broker._call_once(api_id, ORDER_PATH, body, check_return_code=False)
    except TransientError:
        log.exception("응답을 받지 못했습니다. 접수 여부를 확인해야 합니다")
        state.setdefault("unknown_orders", []).append(body)
        return None
    except BrokerError:
        log.exception("주문이 거부됐습니다")
        return None

    data.pop("_headers", None)
    if data.get("return_code") != 0:
        log.warning("거부: %s", data.get("return_msg"))
        state.setdefault("rejected", []).append(data.get("return_msg"))
        return None

    order_no = data.get("ord_no")
    if order_no is None:
        # ord_no 는 아직 실측 못 한 가정이다. 틀렸으면 키 목록이 남는다
        log.error("성공 응답에 ord_no 가 없습니다. 키: %s", sorted(data))
        state["success_keys"] = sorted(data)
    log.info("접수: %s", order_no)
    return order_no


def _cancel(broker: KiwoomBroker, order_no: str | None, code: str) -> dict[str, Any]:
    """`cncl_qty` 가 `"0"` 이면 잔량 전부 취소다."""
    if not order_no:
        return {"skipped": "취소할 주문번호가 없다"}
    try:
        data = broker._call_once(
            CANCEL_API,
            ORDER_PATH,
            {
                "dmst_stex_tp": "KRX",
                "orig_ord_no": order_no,
                "stk_cd": code,
                "cncl_qty": "0",
            },
            check_return_code=False,
        )
    except BrokerError as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    data.pop("_headers", None)
    return _mask(data)


def _limit_body(code: str, quantity: str, price: str) -> dict[str, str]:
    return {
        "dmst_stex_tp": "KRX",
        "stk_cd": code,
        "ord_qty": quantity,
        "ord_uv": price,
        "trde_tp": LIMIT_ORDER,
    }


def _limit_price(broker: KiwoomBroker, args) -> str | None:
    """오늘 하한가. 부호를 뗀 정수로 준다 (`ord_uv` 는 정수만 받는다).

    **장이 열린 뒤에는 대조하지 않는다.** 세션이 이미 넘어가 `lst_pric` 이
    오늘 값이다. 대조는 장 전에 읽을 때만 의미가 있었다 (2026-08-31 실측).

    게다가 `price_daily` 대조는 **아침에 구조적으로 못 맞는다.** KRX 가 D일
    데이터를 D+1 에 공개하므로 `daily` 는 늘 어제를 채우고, 아침의 최신
    일봉은 항상 '그저께' 다. 기준가는 '어제 종가' 라 영원히 어긋난다.
    2026-09-01·09-02 에 연달아 이것 때문에 하한가 주문이 건너뛰어졌고
    15:30 취소 실험이 두 번 무산됐다.

    장 전 경로는 남겨둔다. `--limit-at` 을 09:00 앞으로 옮기면 그때는
    대조가 필요하다.
    """
    if datetime.now(SEOUL).hour >= MARKET_OPEN_HOUR:
        data = broker._call_once(QUOTE_API, INFO_PATH, {"stk_cd": MAIN_CODE})
        low = strip_sign(data["lst_pric"])
        log.info("장중이라 대조 없이 하한가 %s 를 씁니다", low)
        return str(int(low))

    expected = _last_close()
    for attempt in range(1, args.price_attempts + 1):
        data = broker._call_once(QUOTE_API, INFO_PATH, {"stk_cd": MAIN_CODE})
        base = strip_sign(data["base_pric"])
        low = strip_sign(data["lst_pric"])

        if expected is None or base == expected:
            log.info("기준가 %s, 하한가 %s", base, low)
            return str(int(low))

        log.info("기준가가 아직 %s 입니다 (기대 %s)", base, expected)
        if attempt < args.price_attempts:
            time.sleep(args.price_wait_sec)
    return None


def _last_close() -> Decimal | None:
    """`price_daily` 의 마지막 종가. 못 읽으면 대조를 건너뛴다."""
    try:
        with connect() as conn, transaction(conn) as cur:
            row = raw_close(cur, MAIN_STOCK_ID, datetime.now(UTC).date())
    except Exception:
        log.exception("전 거래일 종가를 읽지 못했습니다. 대조를 건너뜁니다")
        return None
    return row[1] if row else None


# --- 보조 --------------------------------------------------------------------


def _rows(data: dict[str, Any], order_no: str | None) -> list[dict[str, Any]]:
    """응답 배열에서 우리 주문번호의 행 **전부**를 뽑는다.

    하나만 찾지 않는다. 여러 행이면 그것이 곧 발견이다.
    """
    if not order_no:
        return []
    found = []
    for value in data.values():
        if not isinstance(value, list):
            continue
        found += [
            _mask(row)
            for row in value
            if isinstance(row, dict) and row.get("ord_no") == order_no
        ]
    return found


def _find(data: dict[str, Any], order_no: str | None) -> dict[str, Any] | None:
    rows = _rows(data, order_no)
    return rows[0] if rows else None


def _sleep_until(at: str, name: str, args) -> None:
    """시각까지 기다린다. **이미 지났으면 바로 한다.**

    늦게 시작해도 남은 단계는 돌아야 한다. 엔진의 시간표 판정과 같은 태도다.

    **한 번 자고 마는 것이 아니라 도달할 때까지 돈다.** 2026-09-01 실측에서
    `min(wait, max_wait_sec)` 로 한 시간만 자고 그대로 진행해, 15:25 단계가
    11:15 에 돌고 15:30 취소 실험이 통째로 무의미해졌다.

    한 번에 자는 시간에 상한을 두는 것은 그대로 둔다. 시계가 크게 어긋났을
    때 하루를 통째로 잃지 않으려는 것이고, 다시 재는 지금은 그 값이
    '얼마나 자주 확인하는가' 가 된다.
    """
    hour, minute = (int(part) for part in at.split(":"))
    while True:
        now = datetime.now(SEOUL)
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        wait = (target - now).total_seconds()
        if wait <= 0:
            log.info("[%s] %s — 실행합니다", at, name)
            return
        log.info("[%s] %s — %.0f초 남았습니다", at, name, wait)
        time.sleep(min(wait, args.max_wait_sec))


def _step(record: dict[str, Any], out: pathlib.Path, name: str, call) -> None:
    """한 단계를 돌리고 **바로 저장한다.**

    끝에 한 번만 저장하면 중간에 죽었을 때 그때까지의 실측이 전부 사라진다.
    """
    entry: dict[str, Any] = {"step": name, "at": _now()}
    try:
        entry["result"] = call()
    except Exception as exc:
        log.exception("%s 실패", name)
        entry["error"] = f"{type(exc).__name__}: {exc}"
    record["steps"].append(entry)
    _save(out, record)


def _save(path: pathlib.Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> str:
    return datetime.now(SEOUL).isoformat()


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m common.broker.testorder")
    parser.add_argument("--out", default="logs/testorder.json")
    parser.add_argument(
        "--force", action="store_true", help=".done 이 있어도 다시 돌린다"
    )
    parser.add_argument(
        "--limit-at", default="09:06", help="하한가 지정가 주문을 낼 시각"
    )
    parser.add_argument("--price-attempts", type=int, default=10)
    parser.add_argument("--price-wait-sec", type=int, default=30)
    # 한 번에 오래 자지 않는다. 시계가 어긋났을 때 하루를 통째로 잃는다
    parser.add_argument("--max-wait-sec", type=int, default=3600)
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
