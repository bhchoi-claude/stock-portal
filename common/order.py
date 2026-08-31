# 주문을 내는 흐름. 기록 → 주문 → 갱신 순서를 지킨다 (INTERFACES.md 2.1)

from __future__ import annotations

import logging
import secrets
import time
from decimal import Decimal

import psycopg

from .broker.base import Broker, OrderRequest, OrderResult
from .db.conn import transaction
from .db.orders import apply_result, record_pending
from .types import OrderType, Side

logger = logging.getLogger(__name__)

# Crockford Base32. I·L·O·U 가 빠져 있어 눈으로 옮겨 적어도 헷갈리지 않는다
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_client_order_id() -> str:
    """ULID 를 만든다. 48비트 밀리초 + 80비트 난수를 26자로 인코딩한다.

    실제 시각을 읽지만 전략 판단이 아니라 식별자 생성이라 `feed.now()` 를
    쓰지 않는다 (CLAUDE.md 2 의 시스템 레벨 예외). 백테스트는 이 경로를
    타지 않는다 — 자체 `Portfolio` 로 체결을 흉내낸다.

    **단조증가를 보장하지 않는다.** 같은 밀리초에 두 번 부르면 순서가 섞일
    수 있다. 시간순은 `created_at` 인덱스가 잡고, 중복은 DB 의 UNIQUE 가
    막는다. 80비트 난수라 충돌은 사실상 없다.
    """
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    # 26자 × 5비트 = 130비트. 앞 2비트는 항상 0 이라 첫 글자가 7 을 넘지 않는다
    return "".join(CROCKFORD[(value >> shift) & 0x1F] for shift in range(125, -1, -5))


def place_order(
    conn: psycopg.Connection,
    broker: Broker,
    *,
    account_id: str,
    stock_id: str,
    side: Side,
    order_type: OrderType,
    quantity: int,
    price: Decimal | None = None,
) -> OrderResult:
    """`INTERFACES.md` 2.1 의 네 단계를 그대로 따른다.

    1. `client_order_id` 생성
    2. `order_request` INSERT (`status='pending'`) — **여기서 커밋한다**
    3. `broker.submit_order()`
    4. 응답으로 갱신

    **2번을 커밋하지 않으면 안 된다.** 주문과 같은 트랜잭션에 두면, 주문은
    나갔는데 예외로 롤백되어 기록이 사라진다. 기록이 없으면 다음에 같은
    주문을 또 낸다. 커밋이 이 함수의 존재 이유다.

    3번에서 예외가 나면 **재시도하지 않는다** (CLAUDE.md 3). 행은
    `pending` 으로 남는다. 접수 여부는 `get_order_status` 로 확인한 뒤
    판단한다. 예외를 그대로 올려보내 호출부가 알게 한다.
    """
    client_order_id = new_client_order_id()

    # 기록이 먼저다. 이 트랜잭션이 커밋된 뒤에야 주문을 낸다
    with transaction(conn) as cur:
        record_pending(
            cur,
            client_order_id=client_order_id,
            account_id=account_id,
            stock_id=stock_id,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
        )

    request = OrderRequest(
        client_order_id=client_order_id,
        account_id=account_id,
        stock_id=stock_id,
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
    )
    try:
        result = broker.submit_order(request)
    except Exception:
        # pending 으로 남는다. 접수됐는지 모르는 상태다
        logger.exception("주문 응답을 받지 못했다: %s %s", stock_id, client_order_id)
        raise

    with transaction(conn) as cur:
        apply_result(cur, result)
    return result
