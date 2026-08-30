# 키움 API 실제 응답을 떠보는 도구. 파서를 추측으로 쓰지 않기 위한 것이다

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from .errors import BrokerError
from .kiwoom import KiwoomBroker

# 값에 계좌번호가 담기는 키. 통째로 가린다 (CLAUDE.md 로깅 규칙)
SECRET_HINTS = ("acnt", "account")


def main(argv: list[str] | None = None) -> int:
    """`python -m common.broker.probe kt00018 /api/dostk/acnt '{"qry_tp":"1"}'`

    응답 필드 이름을 눈으로 확인하고 파서를 쓴다. 문서만 보고 필드를 정하면
    조용히 틀린 값을 읽는다. 이 프로젝트의 다른 API 도 전부 실측으로 정했다.
    """
    args = _parse(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        body = json.loads(args.body)
    except json.JSONDecodeError as exc:
        print(f"body 가 JSON 이 아닙니다: {exc}")
        return 2

    # **기본이 모의투자다.** 실전은 명시적으로 골라야 한다
    broker = KiwoomBroker(is_paper=not args.live)
    try:
        data = broker._call(args.api_id, args.path, body)
    except BrokerError as exc:
        # 거부 사유도 정보다. 어떤 파라미터가 빠졌는지 키움이 알려준다
        print(f"{type(exc).__name__}: {exc}")
        return 1

    data.pop("_headers", None)
    print(json.dumps(_mask(data), ensure_ascii=False, indent=2))
    return 0


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m common.broker.probe")
    parser.add_argument("api_id", help="예: kt00018")
    parser.add_argument("path", help="예: /api/dostk/acnt")
    parser.add_argument("body", nargs="?", default="{}", help="요청 바디 JSON")
    parser.add_argument(
        "--live",
        action="store_true",
        help="실전 계좌로 호출한다. 기본은 모의투자다",
    )
    return parser.parse_args(argv)


def _mask(value: Any) -> Any:
    """계좌번호가 담긴 키를 가린다. 응답을 그대로 붙여넣어도 새지 않게."""
    if isinstance(value, dict):
        return {
            key: "***"
            if any(hint in key.lower() for hint in SECRET_HINTS)
            else _mask(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_mask(item) for item in value]
    return value


if __name__ == "__main__":
    sys.exit(main())
