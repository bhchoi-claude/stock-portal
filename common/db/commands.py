# command 테이블 접근. 포털이 엔진을 제어하는 유일한 통로다 (CLAUDE.md 8)

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import psycopg
from psycopg.types.json import Jsonb

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


@dataclass(frozen=True)
class CommandView:
    """화면이 보는 명령 한 건. 눌린 버튼이 처리됐는지 여기서 본다."""

    command_id: int
    target: str
    action: str
    params: dict[str, Any] | None
    status: str
    issued_by: str | None
    result: str | None
    created_at: datetime
    completed_at: datetime | None


def enqueue(
    cur: psycopg.Cursor,
    *,
    target: str,
    action: str,
    params: dict[str, Any] | None = None,
    issued_by: str | None = None,
) -> int:
    """명령을 넣는다. **포털이 엔진을 제어하는 유일한 방법이다** (CLAUDE.md 8).

    포털은 넣기만 하고 결과를 기다리지 않는다. 엔진이 다음 폴링에 집는다.
    눌렀는지 처리됐는지는 `status` 로 화면에서 본다.
    """
    if action not in ACTIONS:
        raise ValueError(f"모르는 명령입니다: {action}")

    cur.execute(
        "INSERT INTO command (target, action, params, issued_by)"
        " VALUES (%s, %s, %s, %s) RETURNING command_id",
        (target, action, Jsonb(params) if params else None, issued_by),
    )
    row = cur.fetchone()
    assert row is not None  # RETURNING 이 있으므로 항상 한 행이다
    return int(row[0])


def recent_commands(cur: psycopg.Cursor, target: str, limit: int) -> list[CommandView]:
    """최근 명령. `'all'` 로 보낸 것도 함께 준다."""
    cur.execute(
        "SELECT command_id, target, action, params, status, issued_by, result,"
        " created_at, completed_at"
        " FROM command WHERE target IN (%s, 'all')"
        " ORDER BY created_at DESC, command_id DESC LIMIT %s",
        (target, limit),
    )
    return [CommandView(*row) for row in cur.fetchall()]
