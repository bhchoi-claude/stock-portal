# 텔레그램 설정이 실제로 동작하는지 확인하는 테스트 발송 CLI

from __future__ import annotations

import logging
import sys

from .telegram import TelegramNotifier


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        notifier = TelegramNotifier.from_env()
    except RuntimeError as exc:
        print(exc)
        return 2

    if not notifier.send("INFO", "테스트 알림", "stock-portal 알림 설정 확인"):
        print("발송에 실패했습니다. 위 로그를 확인하세요.")
        return 1

    print("발송했습니다. 텔레그램을 확인하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
