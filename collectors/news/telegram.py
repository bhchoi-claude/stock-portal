# 텔레그램 채널을 상시 수신해 raw_message 에 적재하는 프로세스

from __future__ import annotations

import asyncio
import hashlib
import logging
import sys
from typing import Any

from telethon import TelegramClient, events, utils
from telethon.tl.types import PeerChannel

from common.config import load_config
from common.db.conn import connect, transaction
from common.db.events import log_event
from common.db.heartbeat import upsert_heartbeat
from common.db.messages import (
    RawMessage,
    TelegramSource,
    insert_messages,
    last_external_id,
    telegram_sources,
)
from common.env import require_env

from . import SESSION_PATH

logger = logging.getLogger(__name__)

PROCESS = "telegram"


def to_record(source_id: int, message: Any) -> RawMessage | None:
    """텔레그램 메시지를 적재 형식으로 바꾼다. 본문이 없으면 None.

    사진·파일만 있는 메시지는 본문이 비어 있다. 분석할 것이 없으므로
    적재하지 않는다. 캡션이 달려 있으면 그것이 본문으로 들어온다.
    """
    text = (message.message or "").strip()
    if not text:
        return None
    return RawMessage(
        source_id=source_id,
        external_id=str(message.id),
        content=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        # Telethon 이 UTC 를 준다. 저장도 UTC 다 (CLAUDE.md 5)
        published_at=message.date,
    )


async def catch_up(client: TelegramClient, source: TelegramSource, limit: int) -> int:
    """끊긴 동안의 메시지를 따라잡는다. 적재된 건수를 돌려준다.

    이미 받은 것이 있으면 그 번호 뒤를 **전부** 받는다. 며칠 꺼져 있었다면
    그만큼 많이 받는다. 개수를 제한하면 조용히 구멍이 남는다.

    처음 붙는 채널만 limit 으로 최근 것부터 받는다. 채널의 전체 역사를
    받을 이유가 없다.
    """
    with connect() as conn, transaction(conn) as cur:
        since = last_external_id(cur, source.source_id)

    kwargs = {"min_id": since} if since else {"limit": limit}
    records = []
    async for message in client.iter_messages(PeerChannel(source.channel_id), **kwargs):
        record = to_record(source.source_id, message)
        if record:
            records.append(record)

    with connect() as conn, transaction(conn) as cur:
        return insert_messages(cur, records)


def beat(
    status: str, detail: dict[str, Any] | None = None, *, restart: bool = False
) -> None:
    """생존 신호. 쿼리가 짧아 이벤트 루프를 막지 않는다."""
    with connect() as conn, transaction(conn) as cur:
        upsert_heartbeat(cur, PROCESS, status, detail=detail, restart=restart)


async def beat_loop(interval: int, counter: dict[str, int]) -> None:
    """상시 프로세스라 주기적으로 살아 있다고 적는다.

    배치와 달리 끝나는 시점이 없다. 마지막 신호가 언제인지로만 판단한다.
    """
    while True:
        await asyncio.sleep(interval)
        beat("running", {"stored": counter["stored"]})


async def run(params: dict[str, Any]) -> int:
    with connect() as conn, transaction(conn) as cur:
        sources = telegram_sources(cur)
    if not sources:
        print("활성 텔레그램 채널이 없습니다. config/sources.yaml 을 확인하세요.")
        return 1

    client = TelegramClient(
        str(SESSION_PATH),
        int(require_env("TELEGRAM_API_ID")),
        require_env("TELEGRAM_API_HASH"),
    )
    await client.connect()
    if not await client.is_user_authorized():
        # 여기서 로그인을 물으면 systemd 아래에서 그대로 멈춘다
        print("세션이 만료됐습니다. python -m collectors.news.login 을 실행하세요.")
        return 1

    beat("running", restart=True)

    counter = {"stored": 0}
    for source in sources:
        stored = await catch_up(client, source, params["catch_up_limit"])
        counter["stored"] += stored
        logger.info("%s 따라잡기 %d건", source.name, stored)

    by_peer = {utils.get_peer_id(PeerChannel(s.channel_id)): s for s in sources}

    @client.on(events.NewMessage(chats=list(by_peer)))
    async def on_message(event) -> None:
        source = by_peer.get(event.chat_id)
        if source is None:
            return
        record = to_record(source.source_id, event.message)
        if record is None:
            return
        with connect() as conn, transaction(conn) as cur:
            counter["stored"] += insert_messages(cur, [record])

    with connect() as conn, transaction(conn) as cur:
        log_event(
            cur,
            PROCESS,
            "INFO",
            "텔레그램 수집 시작",
            category="collect",
            detail={"channels": len(sources), "catch_up": counter["stored"]},
        )

    logger.info("채널 %d개 수신 대기", len(sources))
    asyncio.ensure_future(beat_loop(params["heartbeat_sec"], counter))
    await client.run_until_disconnected()
    return 0


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    if not SESSION_PATH.exists():
        print("세션이 없습니다. python -m collectors.news.login 을 먼저 실행하세요.")
        return 1

    params = load_config("collect")["news"]
    try:
        return asyncio.run(run(params))
    except BaseException as exc:
        beat("error", {"error": f"{type(exc).__name__}: {exc}"})
        raise


if __name__ == "__main__":
    sys.exit(main(sys.argv))
