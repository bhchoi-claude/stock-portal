# 정보수집 수집기. 텔레그램과 DART (PROJECT.md 9장)

from common.env import PROJECT_ROOT

# 로그인 자격이 들어 있다. .gitignore 에 있고 권한은 600 이다.
# 로그인 CLI 와 수집 프로세스가 같은 파일을 쓴다
SESSION_PATH = PROJECT_ROOT / "telegram.session"
