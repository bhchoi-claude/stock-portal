# config/ 의 계좌·소스 정의를 기준 데이터 테이블에 적재하는 CLI

from __future__ import annotations

import logging
import sys
from decimal import Decimal
from typing import Any

from ..config import load_config
from . import master
from .conn import connect, transaction
from .events import log_event
from .models import Account, Source

logger = logging.getLogger(__name__)


def build_accounts(config: dict[str, Any]) -> list[Account]:
    """accounts.yaml 을 Account 로 바꾼다.

    account 테이블에 있는 컬럼만 골라 담는다. allocation 은 자금 배분이라
    RiskManager 가 config 에서 직접 읽고, 계좌번호는 .env 밖으로 나오지 않는다.
    """
    return [
        Account(
            account_id=account_id,
            broker=spec["broker"],
            strategy=spec["strategy"],
            is_paper=bool(spec.get("is_paper", False)),
            currency=spec.get("currency", "KRW"),
            is_active=bool(spec.get("is_active", True)),
        )
        for account_id, spec in (config.get("accounts") or {}).items()
    ]


def build_sources(config: dict[str, Any]) -> list[Source]:
    """sources.yaml 을 Source 로 바꾼다."""
    return [
        Source(
            kind=spec["kind"],
            identifier=spec["identifier"],
            name=spec["name"],
            # YAML 은 1.0 을 float 으로 읽는다. Decimal(float) 은 오차가 섞인다
            weight=Decimal(str(spec.get("weight", "1.0"))),
            is_active=bool(spec.get("is_active", True)),
        )
        for spec in (config.get("sources") or [])
    ]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        accounts = build_accounts(load_config("accounts"))
        sources = build_sources(load_config("sources"))
    except RuntimeError as exc:
        print(exc)
        return 2
    except KeyError as exc:
        print(f"설정에 {exc} 항목이 없습니다.")
        return 2

    with connect() as conn, transaction(conn) as cur:
        master.upsert_accounts(cur, accounts)
        master.upsert_sources(cur, sources)
        log_event(
            cur,
            "seed",
            "INFO",
            "기준 데이터 적재",
            category="system",
            detail={"accounts": len(accounts), "sources": len(sources)},
        )

    print(f"계좌 {len(accounts)}건, 소스 {len(sources)}건 적재했습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
