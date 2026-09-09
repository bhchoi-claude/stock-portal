# Phase 8 완료 기준(주문 취소·비상 중단)을 자동으로 검증하는 임시 도구
#
# **완료 기준이 채워지면 지운다.** 두 기준은 시장에 맡기면 몇 주가 지나도
# 발화하지 않는다 — 시장가 주문은 즉시 체결돼 미체결이 안 생기고, 비상
# 중단은 사람이 눌러야 한다. 사용자가 낮에 화면을 못 보므로 자동으로 만든다.
#
#   limit   체결되지 않을 지정가 매수 1주. 15:10 취소 단계의 먹잇감이다
#   halt    진입 차단 명령. 포털 버튼이 넣는 것과 같은 행이다
#   verify  둘의 결과를 모아 텔레그램으로 보낸다

from __future__ import annotations

import sys
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from common.broker.kiwoom import KiwoomBroker
from common.config import load_config
from common.db.commands import enqueue
from common.db.conn import connect, load_database_url, transaction
from common.notify.telegram import TelegramNotifier
from common.order import place_order
from common.types import OrderType, Side

SEOUL = ZoneInfo("Asia/Seoul")

# 유동성이 크고 지금 보유가 없다. 잘못 체결돼도 1주다
STOCK = "KRX:005930"

# 하한가(-30%) 안쪽이라 접수는 되고 체결은 안 된다.
# -40% 로 하면 가격제한폭 밖이라 주문 자체가 거부된다
DISCOUNT = Decimal("0.80")

# 1000 은 모든 호가단위(1·5·10·50·100·500·1000)의 배수다.
# 호가단위를 벗어난 가격은 거부된다
TICK = 1000


def _swing() -> dict[str, Any]:
    return load_config("engine")["swing"]


def limit() -> None:
    """체결되지 않을 지정가 매수를 낸다. 미체결을 만드는 것이 목적이다."""
    swing = _swing()
    account = load_config("accounts")["accounts"][swing["account_id"]]
    broker = KiwoomBroker(is_paper=account["is_paper"])

    price = broker.get_quote(STOCK).price * DISCOUNT
    price = Decimal(int(price) // TICK * TICK)

    # place_order 가 트랜잭션을 직접 잡는다. autocommit 커넥션을 주면 안 된다
    with psycopg.connect(load_database_url()) as conn:
        result = place_order(
            conn,
            broker,
            account_id=swing["account_id"],
            stock_id=STOCK,
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity=1,
            price=price,
        )
    print(f"지정가 {price} 주문: {result.status}")


def halt() -> None:
    """진입 차단 명령을 넣는다. 엔진이 폴링해서 집어간다."""
    with connect() as conn, transaction(conn) as cur:
        command_id = enqueue(
            cur,
            target=_swing()["process_name"],
            action="halt_entry",
            issued_by="p8test",
        )
    print("halt_entry 명령", command_id)


def verify() -> None:
    """두 기준의 결과를 모아 보낸다. 하나라도 어긋나면 WARN 으로 간다."""
    today = datetime.now(SEOUL).date()
    checks: list[tuple[bool, str]] = []

    with connect() as conn, transaction(conn) as cur:
        checks.append(_check_cancel(cur, today))
        checks.append(_check_command(cur, today))
        checks.append(_check_heartbeat(cur))

    body = f"{today}\n\n" + "\n".join(
        f"{'OK  ' if ok else 'FAIL'} {text}" for ok, text in checks
    )
    body += "\n\n16:10 에 엔진을 재시작해 차단을 푼다."
    print(body)

    passed = all(ok for ok, _ in checks)
    TelegramNotifier.from_env().send("INFO" if passed else "WARN", "Phase 8 검증", body)


def _check_cancel(cur, today) -> tuple[bool, str]:
    """15:10 취소 단계가 미체결 지정가를 취소했는가."""
    cur.execute(
        "SELECT status, filled_qty FROM order_request"
        " WHERE stock_id = %s AND order_type = 'LIMIT'"
        "   AND (created_at AT TIME ZONE 'Asia/Seoul')::date = %s"
        " ORDER BY order_id DESC LIMIT 1",
        (STOCK, today),
    )
    row = cur.fetchone()
    if row is None:
        return False, "주문 취소 - 오늘 낸 지정가 주문이 없다"
    return row[0] == "cancelled", f"주문 취소 - 지정가가 {row[0]}"


def _check_command(cur, today) -> tuple[bool, str]:
    """엔진이 명령을 집어 처리를 끝냈는가."""
    cur.execute(
        "SELECT status, result FROM command WHERE action = 'halt_entry'"
        "   AND (created_at AT TIME ZONE 'Asia/Seoul')::date = %s"
        " ORDER BY command_id DESC LIMIT 1",
        (today,),
    )
    row = cur.fetchone()
    if row is None:
        return False, "비상 중단 - 오늘 넣은 명령이 없다"
    return row[0] == "done", f"비상 중단 - 명령 {row[0]}: {row[1]}"


def _check_heartbeat(cur) -> tuple[bool, str]:
    """**엔진이 실제로 차단 상태인가.** 명령이 done 이어도 이것이 진짜다."""
    cur.execute(
        "SELECT detail FROM heartbeat WHERE process_name = %s",
        (_swing()["process_name"],),
    )
    row = cur.fetchone()
    state = (row[0] if row else None) or {}
    return bool(state.get("halt_entry")), f"엔진 차단 상태 - heartbeat {state}"


if __name__ == "__main__":
    {"limit": limit, "halt": halt, "verify": verify}[sys.argv[1]]()
