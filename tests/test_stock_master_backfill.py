# 과거 스냅샷에서 폐지 종목을 역산하는 로직을 확인한다

from datetime import date

import pytest

from collectors.market.stock_master_backfill import (
    SnapshotShrank,
    gather,
    snapshot_dates,
    with_delisted,
)
from common.db.models import Stock


def stock(code: str, name: str = "테스트") -> Stock:
    return Stock(
        stock_id=f"KRX:{code}", exchange="KRX", code=code, board="KOSPI", name=name
    )


def test_스냅샷_날짜는_마지막에_종료일을_포함한다():
    dates = snapshot_dates(date(2026, 8, 1), date(2026, 8, 26), 7)

    assert dates == [
        date(2026, 8, 1),
        date(2026, 8, 8),
        date(2026, 8, 15),
        date(2026, 8, 22),
        date(2026, 8, 26),
    ]


def test_종료일이_간격에_딱_맞아도_한_번만_넣는다():
    dates = snapshot_dates(date(2026, 8, 1), date(2026, 8, 15), 7)

    assert dates == [date(2026, 8, 1), date(2026, 8, 8), date(2026, 8, 15)]


def test_마지막_스냅샷에_있으면_폐지일이_없다():
    latest = {"KRX:005930": stock("005930")}
    last_seen = {"KRX:005930": date(2026, 8, 26)}

    assert with_delisted(latest, last_seen, date(2026, 8, 26))[0].delisted_at is None


def test_마지막_스냅샷에_없으면_마지막_관측일_다음날이_폐지일이다():
    latest = {"KRX:001880": stock("001880", "DL건설")}
    last_seen = {"KRX:001880": date(2024, 1, 10)}

    result = with_delisted(latest, last_seen, date(2026, 8, 26))

    assert result[0].delisted_at == date(2024, 1, 11)


def test_종목의_최종_상태가_남는다(monkeypatch):
    # 이름이 바뀐 종목은 마지막으로 관측된 이름이 남아야 한다
    snapshots = {
        "20260801": [stock("005930", "옛이름")],
        "20260808": [stock("005930", "새이름")],
    }
    monkeypatch.setattr(
        "collectors.market.stock_master_backfill.collect", lambda d: snapshots[d]
    )

    latest, _, final_day = gather([date(2026, 8, 1), date(2026, 8, 8)], 0, 0.1)

    assert latest["KRX:005930"].name == "새이름"
    assert final_day == date(2026, 8, 8)


def test_휴장일은_다음날로_민다(monkeypatch):
    # 8/15 는 0건이고 8/16 에 데이터가 있다
    snapshots = {"20260815": [], "20260816": [stock("005930")]}
    monkeypatch.setattr(
        "collectors.market.stock_master_backfill.collect", lambda d: snapshots[d]
    )

    _, last_seen, final_day = gather([date(2026, 8, 15)], 3, 0.1)

    assert final_day == date(2026, 8, 16)
    assert last_seen["KRX:005930"] == date(2026, 8, 16)


def test_종목_수가_급감하면_중단한다(monkeypatch):
    # API 이상 응답을 '전 종목 폐지' 로 읽으면 stock 이 통째로 망가진다
    snapshots = {
        "20260801": [stock(f"{i:06d}") for i in range(100)],
        "20260808": [stock("000001")],
    }
    monkeypatch.setattr(
        "collectors.market.stock_master_backfill.collect", lambda d: snapshots[d]
    )

    with pytest.raises(SnapshotShrank):
        gather([date(2026, 8, 1), date(2026, 8, 8)], 0, 0.1)


def test_소폭_감소는_통과한다(monkeypatch):
    snapshots = {
        "20260801": [stock(f"{i:06d}") for i in range(100)],
        "20260808": [stock(f"{i:06d}") for i in range(95)],
    }
    monkeypatch.setattr(
        "collectors.market.stock_master_backfill.collect", lambda d: snapshots[d]
    )

    latest, _, _ = gather([date(2026, 8, 1), date(2026, 8, 8)], 0, 0.1)

    assert len(latest) == 100


def test_사라졌다_다시_나타나면_폐지가_아니다(monkeypatch):
    # 마지막 관측일만 보므로 재등장이 자연스럽게 처리된다
    snapshots = {
        "20260801": [stock("005930"), stock("000660")],
        "20260808": [stock("005930")],
        "20260815": [stock("005930"), stock("000660")],
    }
    monkeypatch.setattr(
        "collectors.market.stock_master_backfill.collect", lambda d: snapshots[d]
    )

    latest, last_seen, final_day = gather(
        [date(2026, 8, 1), date(2026, 8, 8), date(2026, 8, 15)], 0, 0.5
    )
    result = {s.stock_id: s for s in with_delisted(latest, last_seen, final_day)}

    assert result["KRX:000660"].delisted_at is None
