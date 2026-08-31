# command 테이블 접근. 포털이 엔진을 제어하는 유일한 통로다 (CLAUDE.md 8)

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import psycopg

# 화면 버튼이 넣는 값. 엔진이 모르는 action 은 실패로 닫는다
Action = Literal["stop", "halt_entry", "liquidate_all", "close_position"]

ACTIONS: tuple[str, ...] = ("stop", "halt_entry", "liquidate_all", "close_position")


@dataclass(frozen=True)
class Command:
    """command 한 행. 엔진이 폴링해서 읽는다."""

    command_id: int
    target: str
    action: str
    params: dict[str, Any] | None
    issued_by: str | None
    created_at: datetime


def pending_commands(cur: psycopg.Cursor, process_name: str) -> list[Command]:
    """이 프로세스가 처리할 미처리 명령. 오래된 것부터 준다.

    `target` 이 `'all'` 인 것도 받는다. 전체 정지 같은 것이 그 경로다.
    `idx_command_pending` 이 `(target, created_at) WHERE status = 'pending'`
    이라 이 조회를 그대로 탄다.
    """
    cur.execute(
        "SELECT command_id, target, action, params, issued_by, created_at"
        " FROM command"
        " WHERE status = 'pending' AND target IN (%s, 'all')"
        " ORDER BY created_at, command_id",
        (process_name,),
    )
    return [Command(*row) for row in cur.fetchall()]


def ack(cur: psycopg.Cursor, command_id: int) -> None:
    """받았다고 표시한다. **처리하기 전에 찍는다.**

    순서가 바뀌면 처리 중에 죽었을 때 다음 폴링이 같은 명령을 또 집는다.
    전량청산이 두 번 도는 것이 그 사고다.

    `status = 'pending'` 을 조건에 둬서 두 번 받지 않는다.
    """
    cur.execute(
        "UPDATE command SET status = 'acked', acked_at = NOW()"
        " WHERE command_id = %s AND status = 'pending'",
        (command_id,),
    )


def complete(
    cur: psycopg.Cursor, command_id: int, *, ok: bool, result: str | None = None
) -> None:
    """처리 결과를 남긴다. 화면이 이 값을 보여준다."""
    cur.execute(
        "UPDATE command SET status = %s, completed_at = NOW(), result = %s"
        " WHERE command_id = %s",
        ("done" if ok else "failed", result, command_id),
    )
