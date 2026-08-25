# 마이그레이션 SQL 파일을 순서대로 적용하고 적용 이력을 schema_migration 에 남기는 러너

from __future__ import annotations

import hashlib
import pathlib
import sys

MIGRATIONS_DIR = pathlib.Path(__file__).parent / "migrations"

BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migration (
    version     TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    checksum    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def migration_files() -> list[pathlib.Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def checksum(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def version_of(path: pathlib.Path) -> str:
    return path.name.split("_", 1)[0]


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "status"
    if command not in ("status", "apply"):
        print("사용법: python -m common.db.migrate [status|apply]")
        return 2

    try:
        import psycopg
    except ImportError:
        print("psycopg 가 없습니다. pip install -r requirements.txt 를 실행하세요.")
        return 2

    from .conn import load_database_url

    try:
        database_url = load_database_url()
    except RuntimeError as exc:
        print(exc)
        return 2

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(BOOTSTRAP)
            cur.execute("SELECT version, checksum FROM schema_migration")
            applied = dict(cur.fetchall())
        conn.commit()

        pending: list[tuple[str, pathlib.Path, str]] = []
        tampered: list[str] = []

        for path in migration_files():
            version, digest = version_of(path), checksum(path)
            if version not in applied:
                print(f"  대기    {path.name}")
                pending.append((version, path, digest))
            elif applied[version] != digest:
                print(f"  변경됨  {path.name}")
                tampered.append(path.name)
            else:
                print(f"  적용됨  {path.name}")

        if tampered:
            print(
                f"\n적용된 뒤 내용이 바뀐 파일이 있습니다: {', '.join(tampered)}\n"
                "이미 적용된 마이그레이션은 수정하지 않는다. 새 파일을 추가한다."
            )
            return 1

        if command == "status":
            print(f"\n대기 {len(pending)}건")
            return 0

        for version, path, digest in pending:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(path.read_text(encoding="utf-8"))
                cur.execute(
                    "INSERT INTO schema_migration (version, filename, checksum)"
                    " VALUES (%s, %s, %s)",
                    (version, path.name, digest),
                )
            print(f"  적용    {path.name}")

        print(f"\n{len(pending)}건 적용 완료.")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
