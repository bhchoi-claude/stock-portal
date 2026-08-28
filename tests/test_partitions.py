# 파티션 월 목록 계산을 확인한다

from datetime import UTC, datetime

from collectors.market.partitions import months_from


def test_이번_달부터_센다():
    months = months_from(datetime(2026, 8, 28, 12, 0, tzinfo=UTC), 3)

    assert months == [
        datetime(2026, 8, 1, tzinfo=UTC),
        datetime(2026, 9, 1, tzinfo=UTC),
        datetime(2026, 10, 1, tzinfo=UTC),
    ]


def test_연말을_넘어간다():
    months = months_from(datetime(2026, 11, 15, tzinfo=UTC), 4)

    assert [f"{m:%Y%m}" for m in months] == ["202611", "202612", "202701", "202702"]


def test_경계는_utc_다():
    # 파티션 경계가 UTC 라 여기서도 UTC 로 세야 한 달이 어긋나지 않는다
    months = months_from(datetime(2026, 8, 31, 23, 0, tzinfo=UTC), 1)

    assert months[0] == datetime(2026, 8, 1, tzinfo=UTC)
    assert months[0].tzinfo is UTC


def test_한_달만_요청하면_한_달만_돌려준다():
    assert len(months_from(datetime(2026, 8, 1, tzinfo=UTC), 1)) == 1
