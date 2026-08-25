# config 를 모델로 바꾸는 과정에서 비밀값이 새거나 값이 뭉개지지 않는지 확인한다

from dataclasses import fields
from decimal import Decimal

import pytest
import yaml

from common.config import CONFIG_DIR
from common.db.models import Account
from common.db.seed import build_accounts, build_sources


def example_accounts() -> dict:
    """커밋된 예시 파일을 그대로 쓴다. 형식이 깨지면 여기서 잡힌다."""
    return yaml.safe_load(
        (CONFIG_DIR / "accounts.example.yaml").read_text(encoding="utf-8")
    )


def test_예시_계좌_파일이_모델로_변환된다():
    accounts = build_accounts(example_accounts())

    assert {a.account_id for a in accounts} == {"daytrade", "swing", "paper"}
    assert [a.is_paper for a in accounts if a.account_id == "paper"] == [True]


def test_계좌번호는_모델에_담기지_않는다():
    # 설정에 계좌번호가 섞여 들어와도 DB 로 나가지 않아야 한다 (SCHEMA.md 1장)
    config = example_accounts()
    config["accounts"]["swing"]["account_no"] = "1234567890"

    account = next(a for a in build_accounts(config) if a.account_id == "swing")

    assert "1234567890" not in str(account)
    assert not any(f.name == "account_no" for f in fields(Account))


def test_자금_배분은_db_로_가지_않는다():
    # allocation 은 config 에만 있다. account 테이블에는 컬럼이 없다
    assert not any(f.name == "allocation" for f in fields(Account))


def test_소스_가중치는_decimal_로_읽는다():
    # YAML 의 1.0 은 float 이다. 그대로 Decimal 에 넣으면 오차가 섞인다
    sources = build_sources(
        {"sources": [{"kind": "dart", "identifier": "d", "name": "n", "weight": 1.1}]}
    )

    assert sources[0].weight == Decimal("1.1")


def test_커밋된_소스_파일이_모델로_변환된다():
    config = yaml.safe_load((CONFIG_DIR / "sources.yaml").read_text(encoding="utf-8"))
    sources = build_sources(config)

    assert [(s.kind, s.identifier) for s in sources] == [("dart", "dart")]


def test_빈_설정은_빈_목록이다():
    assert build_accounts({}) == []
    assert build_sources({}) == []


def test_필수_항목이_없으면_keyerror():
    with pytest.raises(KeyError):
        build_sources({"sources": [{"kind": "dart"}]})
