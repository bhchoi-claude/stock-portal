# 텔레그램 사용자 세션을 만드는 1회용 CLI. 로그인은 사람이 직접 한다

from __future__ import annotations

import sys

from telethon.sync import TelegramClient

from common.env import require_env

from . import SESSION_PATH


def main(argv: list[str]) -> int:
    """세션을 만들고, 실제로 읽히는지 채널 목록으로 확인한다.

    이미 세션이 있으면 로그인을 묻지 않고 목록만 다시 보여준다.
    `source` 에 넣을 채널 식별자를 여기서 고르면 된다.
    """
    raw_id = require_env("TELEGRAM_API_ID")
    if not raw_id.isdigit():
        print(f"TELEGRAM_API_ID 가 숫자가 아닙니다: {len(raw_id)}자")
        return 1

    # 봇 토큰이 아니라 사용자 계정이다. 채널을 구독한 계정으로만 읽을 수 있다.
    # 전화번호와 인증코드는 이 프롬프트에서 직접 입력한다
    with TelegramClient(
        str(SESSION_PATH), int(raw_id), require_env("TELEGRAM_API_HASH")
    ) as client:
        me = client.get_me()
        print(f"로그인됨: {me.first_name or ''} @{me.username or '-'} (id {me.id})")
        print(f"세션 파일: {SESSION_PATH}")
        print()
        print("구독 중인 채널")
        print(f"{'channel_id':>14}  {'@username':<24} 이름")

        count = 0
        for dialog in client.iter_dialogs():
            if not dialog.is_channel:
                continue
            username = getattr(dialog.entity, "username", None)
            print(
                f"{dialog.entity.id:>14}  {'@' + username if username else '-':<24} {dialog.name}"
            )
            count += 1

    SESSION_PATH.chmod(0o600)
    print()
    print(f"채널 {count}개.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
