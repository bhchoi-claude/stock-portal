# daily_pnl 테이블 접근. 하루 한 번 계좌 상태를 찍는다 (SCHEMA.md 5장)

from __future__ import annotations

from datetime import date
from decimal import Decimal

import psycopg

from ..types import Balance, Position


def snapshot(
    cur: psycopg.Cursor,
    *,
    trade_date: date,
    balance: Balance,
    positions: list[Position],
) -> None:
    """그날의 계좌 상태를 남긴다. 같은 날 다시 부르면 덮어쓴다.

    **거래일은 시장 현지 기준이다** (CLAUDE.md 5). 호출부가 한국 날짜를
    넘긴다.

    `realized_pnl` 은 채우지 않는다. 당일 실현손익을 내려면 매도 체결마다
    그 종목의 원가가 필요한데, `execution` 적재가 아직 없어 매도 뒤에는
    평단가가 사라진다. **모르는 값을 0 으로 적지 않는다.** 스윙의 주
    지표는 `unrealized_pnl` 이라 지장이 없다 (`SCHEMA.md` 5장).

    `trade_count` 는 그날 만들어져 조금이라도 체결된 주문 수다.
    """
    invested = sum((p.avg_price * p.quantity for p in positions), Decimal(0))

    cur.execute(
        """
        INSERT INTO daily_pnl
            (account_id, trade_date, deposit, eval_amount, total_asset,
             unrealized_pnl, trade_count, currency)
        VALUES (%s, %s, %s, %s, %s, %s, (
            SELECT count(*) FROM order_request
            WHERE account_id = %s AND filled_qty > 0
              AND (created_at AT TIME ZONE 'Asia/Seoul')::date = %s
        ), %s)
        ON CONFLICT (account_id, trade_date) DO UPDATE SET
            deposit        = EXCLUDED.deposit,
            eval_amount    = EXCLUDED.eval_amount,
            total_asset    = EXCLUDED.total_asset,
            unrealized_pnl = EXCLUDED.unrealized_pnl,
            trade_count    = EXCLUDED.trade_count,
            currency       = EXCLUDED.currency
        """,
        (
            balance.account_id,
            trade_date,
            balance.deposit,
            balance.eval_amount,
            balance.total_asset,
            balance.eval_amount - invested,
            balance.account_id,
            trade_date,
            balance.currency,
        ),
    )
