# heartbeat(프로세스 생존 신호) 테이블 접근 함수와 배치 실행 래퍼

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import psycopg
from psycopg.types.json import Jsonb

from .conn import connect, transaction

logger = logging.getLogger(__name__)

Status = Literal["running", "idle", "stopping", "error"]


@dataclass(frozen=True)
class ProcessState:
    """heartbeat 한 행. 살아 있는지 판단은 화면 쪽에서 임계 시간으로 한다."""

    process_name: str
    status: str
    last_beat_at: datetime
    started_at: datetime | None
    detail: dict[str, Any] | None


def upsert_heartbeat(
    cur: psycopg.Cursor,
    process_name: str,
    status: Status,
    *,
    detail: dict[str, Any] | None = None,
    restart: bool = False,
) -> None:
    """생존 신호를 남긴다.

    시각은 전부 DB 시계로 찍는다. 프로세스마다 시계가 다르면 '언제 마지막으로
    살아 있었나' 를 서로 비교할 수 없다.

    NOW() 가 아니라 clock_timestamp() 다. NOW() 는 트랜잭션이 시작한 시각이라
    긴 트랜잭션 안에서 신호를 보내면 실제보다 이른 시각이 남는다.

    `restart` 는 실행이 새로 시작할 때만 참이다. 그때 `started_at` 을 다시
    잡고, 그 뒤의 신호는 기존 값을 그대로 둔다.
    """
    cur.execute(
        """
        INSERT INTO heartbeat (process_name, status, last_beat_at, detail, started_at)
        VALUES (%s, %s, clock_timestamp(), %s, clock_timestamp())
        ON CONFLICT (process_name) DO UPDATE SET
            status       = EXCLUDED.status,
            last_beat_at = clock_timestamp(),
            detail       = EXCLUDED.detail,
            started_at   = CASE WHEN %s
                           THEN clock_timestamp() ELSE heartbeat.started_at END
        """,
        (process_name, status, Jsonb(detail) if detail else None, restart),
    )


def list_heartbeats(cur: psycopg.Cursor) -> list[ProcessState]:
    """기록된 모든 프로세스의 마지막 상태."""
    cur.execute(
        "SELECT process_name, status, last_beat_at, started_at, detail"
        " FROM heartbeat ORDER BY process_name"
    )
    return [ProcessState(*row) for row in cur.fetchall()]


def run_with_heartbeat(
    process_name: str, entry: Callable[[list[str]], int], argv: list[str]
) -> int:
    """배치를 돌리며 시작과 끝을 heartbeat 에 남긴다.

    종료코드 0 만 정상으로 본다. 부분 실패로 1 을 돌려주는 수집기가 있어서
    '돌긴 돌았다' 와 '제대로 끝났다' 를 구분해야 한다.

    oneshot 배치라 상태는 세 가지로만 쓴다.
    시작 `running`, 정상 종료 `idle`, 실패나 예외 `error`.
    """
    _beat(process_name, "running", restart=True)
    try:
        code = entry(argv)
    except BaseException as exc:
        _beat(process_name, "error", detail={"error": f"{type(exc).__name__}: {exc}"})
        raise
    _beat(process_name, "idle" if code == 0 else "error", detail={"exit_code": code})
    return code


def _beat(
    process_name: str,
    status: Status,
    *,
    detail: dict[str, Any] | None = None,
    restart: bool = False,
) -> None:
    """자체 커넥션으로 한 줄 남긴다. 수집기의 트랜잭션과 분리해야 실패도 남는다.

    여기서 예외를 삼킨다. 기록 대상이 DB 이므로 event_log 에도 남길 수 없고,
    생존 신호를 못 남기는 것이 수집 자체를 막아서는 안 된다.
    """
    try:
        with connect() as conn, transaction(conn) as cur:
            upsert_heartbeat(cur, process_name, status, detail=detail, restart=restart)
    except Exception:
        logger.exception("heartbeat 기록 실패: %s (%s)", process_name, status)
