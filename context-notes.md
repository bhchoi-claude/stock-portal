# context-notes.md — 작업 중 내린 결정과 근거

> 설계 결정(A-1~A-6)의 내용과 이유는 `docs/` 와 커밋 메시지에 있다.
> 이 문서는 **구현하면서 내린 판단**만 기록한다. 새로 읽는 사람이 "왜 이렇게 했나"를
> 다시 캐지 않도록 하는 것이 목적이다.

---

## 2026-08-24 — 마이그레이션 작성

### 순수 SQL + 러너를 골랐다 (Alembic 아님)

`SCHEMA.md` 가 이미 손으로 쓴 SQL 이고 그것이 계약이다. Alembic 을 쓰면 모델에서
스키마를 역산하게 되는데, 월 단위 파티셔닝·부분 인덱스·SCD 제약은 결국 raw SQL 로
써야 해서 이점이 줄어든다. 문서와 코드가 어긋날 여지를 없애는 쪽을 택했다.

부작용으로 `common/db/` 모델 계층이 특정 ORM 에 묶이지 않는다. 모델은 나중에
psycopg 로 직접 짜든 SQLAlchemy 를 얹든 자유롭다.

러너는 `schema_migration` 테이블에 `version`, `checksum`, `applied_at` 을 남긴다.
**checksum 은 적용된 파일이 나중에 수정되는 것을 잡기 위한 것이다.** 이미 적용된
마이그레이션은 고치지 않고 새 파일을 추가한다.

### 시드는 정적 데이터만 넣었다

`002_seed_static.sql` 에 `exchange` 1건과 `indicator` 8종만 넣었다. 외부 조회가
필요 없는 고정값이라 스키마와 함께 버전 관리하는 편이 안전하다.

`stock`, `exchange_holiday`, `account`, `source` 는 외부 데이터나 실제 계좌 정보가
있어야 하므로 별도 적재 스크립트로 미뤘다. 마이그레이션이 외부 API 에 의존하면
재현 가능성이 깨진다.

### CHECK 제약을 넣지 않았다

`side`, `status`, `action_type` 등은 `SCHEMA.md` 에 주석으로만 허용값이 적혀 있다.
CHECK 제약을 걸면 잘못된 값을 DB 가 막아주지만, **설계 문서에 없는 제약을 임의로
추가하는 것**이라 넣지 않았다.

넣을지 여부는 별도 판단이 필요하다. 넣는다면 값 집합이 확정된 뒤가 좋다.
지금 걸면 상태값을 하나 추가할 때마다 마이그레이션이 필요해진다.

### 파티션을 2027-12 까지만 만들었다

`price_minute` 은 `PARTITION BY RANGE (ts)` 이고 2026-08 ~ 2027-12 의 17개 파티션을
`DO` 블록으로 생성한다. **파티션 경계는 UTC 오프셋을 명시했다.** 날짜 리터럴만 쓰면
세션 타임존에 따라 경계가 9시간 밀린다. 저장은 UTC 이므로 경계도 UTC 여야 한다.

DEFAULT 파티션은 두지 않았다. 있으면 나중에 새 파티션을 붙일 때 DEFAULT 를
스캔해야 해서 느려진다. 대신 **월 파티션을 미리 만드는 배치가 Phase 2 에 필요하다.**
2028-01 이 오기 전에 없으면 분봉 INSERT 가 실패한다. `checklist.md` 에 남겼다.

### `KOSPI_MA200_GAP` 의 source 를 'derived' 로 넣었다

`SCHEMA.md` 의 `indicator.source` 주석은 `'kofia'|'krx'|'customs'|'ecos'` 를 예시로
든다. 그런데 이 지표는 수집이 아니라 KOSPI 지수에서 **계산하는 파생값**이다
(`ROADMAP.md` Phase 3 에도 "수집이 아닌 파생"으로 적혀 있다).

`'krx'` 로 넣으면 수집기가 KRX 에서 이 지표를 직접 받아오는 것처럼 읽힌다.
정확성을 택해 `'derived'` 를 썼다. **문서의 예시 목록에 없는 값이므로 확인이 필요하다.**

### 검증 범위와 한계

로컬에 PostgreSQL 도 Docker 도 없어 **실행 검증은 하지 못했다.** sqlglot 으로
PostgreSQL 방언 파싱만 했다.

확인한 것.

- 문법 파싱 (001: 47개 구문, 002: 2개 구문)
- 테이블 32개, 컬럼 중복 없음
- FK 참조 대상이 모두 먼저 선언됨
- 인덱스·INSERT 가 존재하는 테이블/컬럼을 가리킴
- 파티션 DDL 17개의 파싱·경계 연속성·이름 중복
- A-1~A-6 결정이 실제로 반영됐는지 항목별 확인

확인하지 못한 것.

- `DO $$ ... $$` 블록의 plpgsql 문법 (sqlglot 이 다루지 못해 생성 결과만 재현 검증)
- 실제 실행 시점의 오류 (타입 불일치, 예약어 충돌, 권한)
- 파티션 부모-자식 제약이 실제로 성립하는지

**2026-08-24 서버 적용 완료.** PostgreSQL 18.6 에서 `apply` 가 정상 동작했다.
테이블 50개(본체 32 + `schema_migration` + 파티션 17), `exchange` 1건, `indicator` 8종 확인.
위의 "확인하지 못한 것" 중 실행 시점 오류와 파티션 제약은 이걸로 해소됐다.

### 예약어를 검토했다

`position`, `value`, `open`, `close`, `level`, `source`, `signal` 은 PostgreSQL 에서
비예약어라 테이블·컬럼명으로 쓸 수 있다. `order` 는 예약어이므로 `SCHEMA.md` 대로
`order_request` 를 유지했다.

실행 검증을 못 했으므로 이 판단도 서버 적용으로 확인해야 한다.

---

## 2026-08-24 — 서버 구성

### venv 를 쓴다

Ubuntu 26.04 는 PEP 668 로 시스템 파이썬에 직접 설치하는 것을 막는다.
`pip install` 이 `externally-managed-environment` 로 실패한다.

`--break-system-packages` 를 쓰지 않고 `~/stock-portal/.venv` 를 만들었다.
24시간 돌릴 서버의 시스템 파이썬을 건드리는 위험을 감수할 이유가 없다.

**systemd 유닛을 쓸 때 `.venv/bin/python` 을 명시해야 한다.**
시스템 파이썬으로 실행하면 psycopg 를 찾지 못한다.

### 서버는 배포 키로 pull 한다

비공개 저장소라 서버에도 인증이 필요하다. 계정 토큰 대신 **읽기 전용 배포 키**를 썼다.
서버는 pull 만 하므로 쓰기 권한이 필요 없고, 키가 새도 저장소 하나로 피해가 제한된다.

`~/.ssh/config` 의 `github-stockportal` 호스트 별칭으로 접근한다.
암호 없는 키다. systemd 가 자동 pull 할 때 암호를 물으면 멈추기 때문이다.

### GitHub 계정이 두 개다

저장소는 `bhchoi-claude` 소유이고, 개발 PC 는 `bhchoihaikorea` 로 인증돼 있다.
`bhchoihaikorea` 를 협업자로 추가해서 push 한다. 커밋 author 도 이쪽이다.

### 공인 IP 는 문서에 적지 않는다

`.env` 의 `KIWOOM_ALLOWED_IP` 하나만 정본으로 둔다.
비밀값은 아니지만 집 네트워크를 특정하는 정보라 계좌번호·키와 같은 방침을 적용했다.
