# engine-swing 진입점. 설정을 읽어 조각을 잇고 상주 루프를 돌린다

from __future__ import annotations

import logging
import sys
from datetime import time
from decimal import Decimal

import psycopg

from common.broker.kiwoom import KiwoomBroker
from common.config import load_config
from common.db.conn import load_database_url
from common.feed.live import LiveFeed
from common.risk import RiskManager
from common.strategy.swing import SwingStrategy

from .engine import SwingEngine

log = logging.getLogger(__name__)

# 설정의 strategy 이름으로 고른다. backtest CLI 와 같은 방식이다
STRATEGIES = {"swing": SwingStrategy}


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    params = load_config("engine")["swing"]
    limits = load_config("limits")
    universe = load_config("universe")

    name = params["strategy"]
    if name not in STRATEGIES:
        print(f"모르는 전략입니다: {name}")
        return 1
    strategy_params = load_config(f"strategy_{name}")

    account_id = params["account_id"]
    accounts = load_config("accounts")["accounts"]
    if account_id not in accounts:
        print(f"accounts.yaml 에 계좌 {account_id} 가 없습니다.")
        return 1
    account = accounts[account_id]

    if not account.get("is_active"):
        # 꺼둔 계좌로 주문이 나가면 안 된다. 실계좌가 이 경로로 걸린다
        print(f"계좌 {account_id} 가 is_active=false 입니다.")
        return 1

    broker = KiwoomBroker(is_paper=account["is_paper"])

    # 커넥션이 둘이다. LiveFeed 는 autocommit 읽기 전용을 요구하고,
    # 주문 기록은 트랜잭션을 직접 잡는다. 공유하면 서로의 상태를 망친다
    url = load_database_url()
    read_conn = psycopg.connect(url, autocommit=True)
    conn = psycopg.connect(url)

    engine = SwingEngine(
        conn=conn,
        feed=LiveFeed(
            read_conn,
            broker,
            close_time=time.fromisoformat(limits["backtest"]["close_time"]),
            universe_size=universe["size"],
            liquidity_days=universe["liquidity_days"],
        ),
        broker=broker,
        strategy=STRATEGIES[name](),
        risk=RiskManager(limits["risk"]),
        strategy_params=strategy_params,
        params=params,
        # 0 이면 무제한. 브로커 잔고를 그대로 쓴다
        allocation=Decimal(str(account.get("allocation", 0))),
    )

    try:
        return engine.run()
    finally:
        conn.close()
        read_conn.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
