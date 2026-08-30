# 텔레그램 수집. 본문이 없는 메시지와 중복 적재를 확인한다

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from collectors.news.telegram import to_record
from common.db.messages import (
    RawMessage,
    insert_messages,
    last_external_id,
    telegram_sources,
)

PUBLISHED = datetime(2026, 8, 29, 1, 0, tzinfo=UTC)


@dataclass
class FakeMessage:
    id: int
    message: str | None
    date: datetime = PUBLISHED


def test_media_only_message_is_skipped():
    """사진만 있는 메시지는 본문이 없다. 분석할 것이 없어 적재하지 않는다."""
    assert to_record(1, FakeMessage(10, None)) is None
    assert to_record(1, FakeMessage(10, "   ")) is None


def test_record_hashes_trimmed_content():
    record = to_record(7, FakeMessage(10, "  삼성전자 급등  "))
    assert record.content == "삼성전자 급등"
    assert record.external_id == "10"
    assert record.source_id == 7
    assert record.published_at == PUBLISHED
    assert record.content_hash == hashlib.sha256("삼성전자 급등".encode()).hexdigest()


def test_insert_skips_duplicates(cur):
    source_id = _a_source(cur)
    first = _record(source_id, 1, "같은 내용")
    assert insert_messages(cur, [first]) == 1
    # 번호가 달라도 내용이 같으면 같은 글이다
    assert insert_messages(cur, [_record(source_id, 2, "같은 내용")]) == 0
    assert insert_messages(cur, [_record(source_id, 3, "다른 내용")]) == 1


def test_last_external_id_compares_as_number(cur):
    """메시지 번호는 텍스트로 저장된다. 문자열로 비교하면 9 가 10 보다 뒤다."""
    source_id = _a_source(cur)
    insert_messages(cur, [_record(source_id, 9, "아홉"), _record(source_id, 10, "열")])
    assert last_external_id(cur, source_id) == 10


def test_sources_skip_non_numeric_identifier(cur):
    """식별자는 채널 숫자 ID 다. @사용자명이 남아 있으면 건너뛴다."""
    cur.execute(
        "INSERT INTO source (kind, identifier, name) VALUES"
        " ('telegram', '@some_name', '이름만 있는 채널')"
    )
    assert all(s.name != "이름만 있는 채널" for s in telegram_sources(cur))


def _a_source(cur) -> int:
    cur.execute(
        "INSERT INTO source (kind, identifier, name)"
        " VALUES ('telegram', '999999', '테스트 채널') RETURNING source_id"
    )
    return cur.fetchone()[0]


def _record(source_id: int, message_id: int, text: str) -> RawMessage:
    return RawMessage(
        source_id=source_id,
        external_id=str(message_id),
        content=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        published_at=PUBLISHED,
    )
