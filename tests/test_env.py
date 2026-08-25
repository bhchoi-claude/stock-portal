# .env 와 환경변수 읽기 규칙을 확인한다

import pytest

from common import env


@pytest.fixture
def dotenv(tmp_path, monkeypatch):
    """임시 .env 를 프로젝트 루트인 척 물려준다."""

    def write(text: str):
        (tmp_path / ".env").write_text(text, encoding="utf-8")
        monkeypatch.setattr(env, "PROJECT_ROOT", tmp_path)
        env._dotenv.cache_clear()

    monkeypatch.setattr(env, "PROJECT_ROOT", tmp_path)
    env._dotenv.cache_clear()
    yield write
    env._dotenv.cache_clear()


def test_환경변수가_dotenv_보다_우선한다(dotenv, monkeypatch):
    dotenv("DATABASE_URL=from-file\n")
    monkeypatch.setenv("DATABASE_URL", "from-environ")

    assert env.load_env("DATABASE_URL") == "from-environ"


def test_dotenv_에서_읽는다(dotenv):
    dotenv("DATABASE_URL=postgresql://u:p@localhost:5432/portal_db\n")

    assert env.load_env("DATABASE_URL") == "postgresql://u:p@localhost:5432/portal_db"


def test_값의_공백과_따옴표를_걷어낸다(dotenv):
    # 붙여넣기로 섞여든 공백 때문에 인증이 실패한 적이 있다 (2026-08-25)
    dotenv('TELEGRAM_BOT_TOKEN="  123:abc  "\n')

    assert env.load_env("TELEGRAM_BOT_TOKEN") == "123:abc"


def test_주석과_빈_줄은_건너뛴다(dotenv):
    dotenv("# 주석\n\nDART_API_KEY=key\n")

    assert env.load_env("DART_API_KEY") == "key"


def test_값_안의_샵은_주석이_아니다(dotenv):
    # 비밀번호에 # 가 들어갈 수 있다
    dotenv("DATABASE_URL=postgresql://u:pa#ss@localhost/db\n")

    assert env.load_env("DATABASE_URL") == "postgresql://u:pa#ss@localhost/db"


def test_빈_값은_없는_것으로_본다(dotenv):
    dotenv("TELEGRAM_CHAT_ID=\n")

    assert env.load_env("TELEGRAM_CHAT_ID") is None
    assert env.load_env("TELEGRAM_CHAT_ID", "fallback") == "fallback"


def test_require_env_는_없으면_예외를_낸다(dotenv):
    dotenv("")

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        env.require_env("TELEGRAM_BOT_TOKEN")
