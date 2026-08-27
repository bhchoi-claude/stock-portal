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

---

## 2026-08-25 — 키움 REST 실측

모의투자 계좌로 토큰 발급과 시세 조회에 성공했다. Phase 0 완료.
확인한 규격은 `INTERFACES.md` 2.4 에 적었다. 여기에는 판단만 남긴다.

### `stk_cd` 는 순수 종목코드다 — 앞서 한 추정이 틀렸다

검색으로 본 `KRX:039490` 형식 예시를 근거로 "키움도 거래소 접두어를 쓰므로
A-1 결정과 일치한다"고 적었는데 **틀렸다.** `ka10001` 에 `KRX:005930` 을 보내면
`return_code = 5` 로 거부되고, `005930` 만 받는다.

A-1 결정 자체는 유효하다. 근거가 이전상장 대응이었지 키움 규격과의 일치가
아니었기 때문이다. 다만 **어댑터가 `stock_id` 에서 접두어를 떼야 한다.**
`stock.code` 를 별도 컬럼으로 보관해둔 것이 여기서 쓰인다.

이 변환은 **브로커 어댑터 안에만** 둔다. 밖으로 새면 전략이 종목코드 형식을
알게 되고, 의존 방향(`INTERFACES.md` 0장)이 깨진다.

### 실전과 모의는 앱키가 별도다

모의 앱키로 실전 도메인에 접속하면 명시적으로 거부된다.
`.env` 를 `KIWOOM_APP_KEY_PAPER` / `KIWOOM_APP_KEY_LIVE` 로 나눴다.

어댑터는 `account.is_paper` 하나로 **도메인과 키를 함께** 골라야 한다.
둘 중 하나만 바꾸면 인증 오류가 난다.

### HTTP 200 이어도 `return_code` 를 봐야 한다

토큰 발급 실패도 HTTP 200 으로 왔다. 에러는 본문의 `return_code` 에 있다.
어댑터의 에러 분류(`TransientError` / `PermanentError`)를 HTTP 상태로만 하면
실패를 성공으로 읽는다.

### 검증 과정에서 겪은 것

`.env` 에 앱키를 넣을 때 붙여넣기로 `` 한 글자만 들어간 적이 있다.
`grep` 으로는 "값이 있음"으로 보여서 놓치기 쉬웠다.
값을 다루는 스크립트는 항상 `.strip()` 을 거치게 한다.

셸에서 `"{\"appkey\":\"$VAR\"}"` 식으로 JSON 을 조립하면 값에 특수문자가
하나만 있어도 깨진다. JSON 은 코드로 만든다.

---

## 2026-08-26 — 기준 데이터 출처 확정, `common/db/` 모델 계층

### 기준 데이터 정본은 KRX 다

`stock` 은 `board`·`listed_shares`·`listed_at`·`is_spac`·`is_preferred` 를 요구한다.
DART `corpCode` 에는 시장 구분과 상장주식수가 없고, 키움 종목 리스트 API 는
아직 실측하지 않았다. 확인되지 않은 규격 위에 적재 스크립트를 쌓지 않는다.

DART 고유번호는 Phase 5 정보수집에서 별도로 매핑한다. 지금 섞지 않는다.

휴장일도 KRX 매매거래일정을 쓴다. 공휴일 달력에는 임시휴장과 조기폐장이 없다.

### ORM 을 쓰지 않는다

`psycopg` 위에 함수를 얹었다. SQLAlchemy 를 넣지 않은 이유는 두 가지다.

스키마가 마이그레이션 SQL 로 이미 확정돼 있어 모델이 정의가 아니라 사본이다.
사본이 둘이면 어긋난다. 그래서 사본을 얇게 두고 어긋남을 테스트로 잡는 쪽을 골랐다.

그리고 `price_minute` 처럼 대량 적재가 필요한 테이블이 있다. ORM 계층은
그 지점에서 어차피 벗겨내게 된다.

### 6개 테이블만 만들었다

`exchange`, `exchange_holiday`, `stock`, `stock_status`, `account`, `source`
그리고 `event_log`. Phase 1 적재에 필요한 것뿐이다.

나머지 26개는 쓰는 시점에 추가한다. 지금 만들면 쓰이기 전에 스키마가 바뀐다.

### `load_database_url` 을 `conn.py` 로 옮겼다

`migrate.py` 에 있던 것을 옮기고 `migrate.py` 가 가져다 쓴다.
`.env` 파싱이 두 곳에 있으면 한쪽만 고쳐진다. 특히 `.strip()` 처리가 그렇다.
(2026-08-25 에 앱키에 보이지 않는 문자가 섞였던 건과 같은 종류의 문제다.)

`migrate.py` 는 `psycopg` 확인이 끝난 **뒤에** 이 import 를 한다.
`conn.py` 가 최상단에서 `psycopg` 를 import 하기 때문에, 위로 올리면
"psycopg 가 없습니다" 안내가 뜨기 전에 ImportError 로 죽는다.

같은 이유로 `common/db/__init__.py` 는 비워 두었다. 여기서 재export 하면
`python -m common.db.migrate` 가 패키지 import 단계에서 `psycopg` 를 요구한다.

### upsert 는 `COALESCE` 로 덮는다

`sector`·`listed_shares`·`listed_at`·`delisted_at` 은 `COALESCE` 를 거친다.
KRX 안에서도 목록마다 주는 필드가 다르다. 필드가 없는 출처로 재적재할 때
`EXCLUDED` 를 그대로 쓰면 기존 값이 NULL 로 지워진다.

`delisted_at` 도 마찬가지다. 한 번 채워진 폐지일을 되돌리지 않는다.
행을 지우지 않기로 한 A-6 결정과 같은 방향이다.

### `stock_status` 는 최초 1행만 연다

`open_stock_status` 는 열린 행(`valid_to IS NULL`)이 없을 때만 삽입한다.
변경 감지와 이력 종료는 Phase 2 의 상태 갱신 배치가 맡는다.
Phase 1 에서 이력 관리까지 만들면 수집기 없이 검증할 방법이 없다.

### 스키마 드리프트 테스트

`tests/test_schema_drift.py` 가 `001_initial.sql` 을 파싱해서 dataclass 필드와
대조한다. DB 없이 돈다.

`get_stock` 이 `Stock(*row)` 로 만들기 때문에 `STOCK_COLUMNS` 순서가 어긋나면
값이 조용히 뒤섞인다. 이 순서도 함께 검사한다.

### DB 통합 테스트는 서버에서만 돈다

`DATABASE_URL` 이 없으면 skip 한다. 개발 PC 에는 `.env` 가 없고,
서버 PostgreSQL 은 localhost 만 수신해서 Tailscale 로도 닿지 않는다(5432 거부).

**따라서 pull 한 뒤 서버에서 한 번 돌려야 실제로 검증된 것이다.**
테스트는 커밋 없이 롤백하므로 운영 데이터를 건드리지 않는다.

## 2026-08-26 (2) — KRX 오픈API 로 결정, 텔레그램 알림

### KRX 는 공식 오픈API 를 쓴다

`data.krx.co.kr` 화면이 쓰는 비공식 JSON 엔드포인트가 더 빠른 길이지만
쓰지 않기로 했다. 이 적재는 서버에서 매일 도는 배치가 된다.
문서가 없는 경로는 예고 없이 바뀌고, 바뀌면 조용히 깨진다.

승인 대기 동안 로더를 미리 쓰지 않는다. **응답을 실측한 뒤에 쓴다.**
어제 키움 `stk_cd` 를 검색 결과로 추정했다가 틀렸다. 같은 실수를 반복하지 않는다.

### `.env` 읽기를 `common/env.py` 로 모았다

`conn.py` 에 이어 텔레그램 토큰·chat_id 까지 필요해지면서 세 번째 파서가
생길 참이었다. `load_env` / `require_env` 로 합치고 `load_database_url` 은
그 위의 한 줄이 됐다.

`.env` 는 한 번만 읽고 캐시한다. 값이 바뀌면 프로세스를 재시작한다.
운영 중에 비밀값이 슬며시 바뀌는 쪽이 더 위험하다.

줄 중간의 `#` 는 주석으로 보지 않는다. DB 비밀번호에 들어갈 수 있다.

### 알림은 평문으로 보낸다

Markdown 을 쓰면 종목명과 본문의 `_ * [` 를 매번 이스케이프해야 하고,
빠뜨리면 발송 자체가 400 으로 실패한다. 알림은 꾸미는 것보다 도착하는 게 먼저다.

### `send` 는 예외를 던지지 않고 bool 을 준다

알림 실패가 매매 엔진이나 수집기를 멈추면 안 된다.
실패 이유는 로그로 남기고 호출부는 계속 간다. (`INTERFACES.md` 9장)

### 토큰이 URL 에 들어 있다

텔레그램은 `/bot{토큰}/sendMessage` 형식이라 URL 자체가 비밀값이다.
그래서 예외를 `%s` 로 그대로 찍지 않고 예외 타입 이름만 남긴다.
`urllib` 의 에러 메시지에 URL 이 섞여 들어오기 때문이다.
`tests/test_telegram.py` 가 로그에 토큰이 없는지 확인한다.

### HTTP 200 과 `ok` 는 다르다

키움에서 겪은 것과 같다. 텔레그램도 본문의 `ok` 가 정본이다.
상태코드만 보면 "chat not found" 를 성공으로 읽는다.

### 중복 억제는 아직 만들지 않았다

`INTERFACES.md` 9.1 의 규칙(국면 전환 시에만, 엔진 중단 최초 1회 등)은
발송 주체가 있어야 검증할 수 있다. 지금은 주체가 없다.
`event_log.notified` 를 쓰는 억제 로직은 Phase 3 국면 알림과 함께 만든다.

## 2026-08-26 (3) — `config/` 신설, 계좌·소스 적재

### 공통 타입은 `common/types.py` 에 둔다 (승인 받음)

`PROJECT.md` 4장 구조에 없던 파일이라 확인을 받고 추가했다.

`Candle`·`Quote`·`Position` 은 `Broker` 와 `Strategy` 가 함께 쓴다.
`common/broker/` 안에 두면 전략과 `BacktestFeed` 가 `common.broker` 를
import 하게 되어 의존 방향이 뒤집힌다. 파일은 브로커 작업 때 만든다.

### 적재 스크립트를 `common/db/seed.py` 에 뒀다

`migrate.py` 와 같은 자리다. `python -m common.db.seed` 로 돈다.
새 디렉토리를 만들 이유가 없었다.

**KRX 로더는 여기 넣지 않는다.** 외부 HTTP 호출은 수집기의 일이고,
`common/db/` 가 바깥 API 를 알기 시작하면 경계가 무너진다.
`seed.py` 는 `config/` 에 있는 정적 정의만 다룬다.

### PyYAML 을 추가했다

`config/*.yaml` 이 설계 전제라 불가피했다. 지금까지 psycopg 하나였다.

### YAML 의 `1.0` 은 float 이다

`weight` 를 `Decimal(str(값))` 으로 감싼다. `Decimal(1.1)` 은
`1.100000000000000088…` 이 된다. 금액은 아니지만 NUMERIC 컬럼이라
왕복에서 값이 흔들리면 비교가 어긋난다.

### 계좌번호가 새지 않는 것을 구조로 보장한다

`build_accounts` 는 `account` 테이블에 있는 6개 키만 골라 담는다.
`accounts.yaml` 에 `account_no` 가 섞여 들어와도 모델에 실릴 자리가 없다.
런타임 검사 대신 테스트로 확인한다 (`test_계좌번호는_모델에_담기지_않는다`).

`allocation`(자금 배분)도 같은 이유로 DB 에 가지 않는다.
`account` 테이블에 컬럼 자체가 없고, RiskManager 가 config 에서 직접 읽는다.

### 계좌 활성 상태의 초기값

`paper` 만 `is_active: true` 로 뒀다. `daytrade` 는 Phase 10,
`swing` 실전은 Phase 9 에서 켠다. 지금 켜두면 켜져 있다는 사실을 잊는다.

### 텔레그램 채널은 비워 뒀다

구독 목록을 모른다. 추측해서 채워 넣지 않았다. 형식만 주석으로 남겼다.

## 2026-08-27 — KRX 오픈API 실측 (1차)

### API 호스트는 `data-dbg.krx.co.kr` 다

`openapi.krx.co.kr` 은 포털 웹사이트다. 여기로 API 를 부르면 404 가 오고,
응답도 JSON 이 아니라 에러 HTML 페이지다.

실제 호출은 `https://data-dbg.krx.co.kr/svc/apis/{path}/{apiId}` 로 간다.
명세 화면의 샘플 블록에 `Host: openapi.krx.co.kr` 라고 적혀 있는데
**그 표기가 실제와 다르다.** 테스트 폼의 `apiUrl` 값이 정본이었다.

### 종목기본정보는 시장별로 API 가 나뉜다

| 시장 | apiId |
|---|---|
| 유가증권 | `stk_isu_base_info` |
| 코스닥 | `ksq_isu_base_info` |
| 코넥스 | `knx_isu_base_info` |

경로 앞부분은 `/svc/apis/sto/`, 인증은 요청 헤더 `AUTH_KEY`,
요청 파라미터는 기준일자 `basDd=YYYYMMDD` 하나뿐이다.

`board` 를 어느 API 를 불렀는지로 정할 수 있다. 응답 필드를 해석할 필요가 없다.

### 인증키 발급과 API 이용신청은 별개다

키가 있어도 API 마다 따로 신청해야 한다. 신청 전에는 401 이 온다.

```
{"respMsg":"Unauthorized API Call","respCode":"401"}
```

**401 을 키 문제로 읽으면 안 된다.** 키는 멀쩡하고 권한이 없는 것이다.
어댑터의 에러 분류에서 이 구분이 필요하다. 401 은 `PermanentError` 다.

401 이 JSON 규격대로 왔다는 것 자체가 경로와 헤더가 맞다는 신호였다.
404 HTML 과 401 JSON 을 구분한 덕분에 호스트를 특정할 수 있었다.

### 응답 필드 12개 (명세 기준, 실데이터 미확인)

`ISU_CD` 표준코드 / `ISU_SRT_CD` 단축코드 / `ISU_NM` 한글 종목명 /
`ISU_ABBRV` 한글 종목약명 / `ISU_ENG_NM` 영문 종목명 / `LIST_DD` 상장일 /
`MKT_TP_NM` 시장구분 / `SECUGRP_NM` 증권구분 / `SECT_TP_NM` 소속부 /
`KIND_STKCERT_TP_NM` 주식종류 / `PARVAL` 액면가 / `LIST_SHRS` 상장주식수

**아직 실데이터를 못 봤다.** 이용신청 승인 후 확인하고 매핑을 확정한다.

### 채울 수 없는 컬럼이 있다

`sector`(업종)가 이 API 에 없다. `SECT_TP_NM`(소속부)는 중견기업부·우량기업부
같은 구분이지 업종이 아니다. NULL 로 두고, 업종을 주는 출처가 생기면 채운다.
upsert 를 `COALESCE` 로 짜둔 것이 여기서 쓰인다.

`is_managed`·`is_suspended` 도 없다. Phase 2 상태 갱신 배치가 채운다.
`delisted_at` 은 이전 적재와의 차집합으로 감지해야 한다. 역시 Phase 2 다.

### 휴장일 API 가 없다

서비스 목록에 매매일정·휴장일이 없다. 2026-08-26 에 "휴장일도 KRX
매매거래일정을 쓴다" 고 적은 것은 틀렸다.

일별매매정보에서 역산하는 쪽으로 간다. 거래일이 데이터로 나오므로,
평일인데 없는 날이 휴장일이다. 별도 출처가 필요 없고 임시휴장·조기폐장까지
자동으로 반영된다. `exchange_holiday` 적재는 Phase 2 로 미룬다.
Phase 1 완료 기준에는 `stock` 적재만 있어 영향이 없다.

## 2026-08-27 (2) — 종목 마스터 로더

실데이터를 받아 매핑을 확정했다. 2026-08-26 기준 KOSPI 944 / KOSDAQ 1823 / KONEX 108.

### `name` 은 `ISU_ABBRV` 다

`ISU_NM` 은 `삼성전자보통주`, `삼성전자1우선주` 처럼 정식 명칭이라 화면에 쓸 수 없다.
`ISU_ABBRV` 가 `삼성전자`, `삼성전자우` 로 우리가 아는 이름이다.

### `board` 는 `MKT_TP_NM` 을 그대로 쓴다

값이 정확히 `KOSPI` / `KOSDAQ` / `KONEX` 다. 변환이 필요 없다.
세 API 응답 전체에서 각각 한 종류만 나오는 것을 확인했으므로,
어느 엔드포인트를 불렀는지와 응답이 일치한다.

### 주권 계열만 적재한다 (승인 받음)

`SECUGRP_NM` 에 여섯 종류가 온다. 이 중 셋만 담는다.

| 담는다 | 버린다 |
|---|---|
| 주권, 외국주권, 주식예탁증권 | 부동산투자회사, 사회간접자본투융자회사, 투자회사 |

`stock` 에 증권구분 컬럼이 없어서다. 섞어 넣으면 나중에 리츠를 걸러낼 근거가
DB 안에 남지 않는다. 컬럼을 추가하는 안도 있었으나 스키마 변경을 피했다.

**리츠를 다루게 되면 재적재가 필요하다.** 그때는 컬럼 추가를 함께 검토한다.

### `is_preferred` 는 '보통주가 아님' 이다 (승인 받음)

`KIND_STKCERT_TP_NM` 값은 `보통주` / `구형우선주` / `신형우선주` / `종류주권` 이다.
`종류주권` 은 엄밀히 우선주가 아니지만 참으로 둔다.
이 플래그의 쓰임이 '보통주가 아닌 것을 걸러내기' 이기 때문이다.
거짓으로 두면 종류주권이 보통주처럼 보인다.

### `is_spac` 은 휴리스틱이다

KRX 응답에 스팩 구분값이 없다. 종목명에 `스팩` 이 들어가는지로 판정한다.
관행상 스팩은 전부 이름에 스팩이 들어가지만 **규격이 보장하는 것이 아니다.**
유니버스 필터에서 이 값에만 의존하지 않는 편이 좋다.

### `sector` 는 여전히 NULL 이다

`SECT_TP_NM` 은 KOSPI 에서 전부 빈 문자열이고 KOSDAQ 은 `우량기업부` 다.
업종이 아니라는 것이 데이터로 확인됐다.

### 거래일 파싱에 `datetime` 을 쓰지 않는다

`date.fromisoformat("20150821")` 로 바로 읽는다. `strptime().date()` 은
ruff 의 DTZ007 에 걸리는데, 여기서는 그 경고가 오탐이다. 거래일은 시장 현지
기준 DATE 라서 tz 를 붙이면 오히려 틀린다. (`CLAUDE.md` 절대규칙 5)

경고를 억제하는 대신 `datetime` 을 아예 거치지 않게 바꿨다.
UTC 변환 유혹이 생길 자리를 없애는 쪽이 낫다.

### 위치는 `collectors/market/` 다

외부 HTTP 호출이라 `common/db/` 에 두지 않는다.
`krx.py` 클라이언트는 Phase 2 일별매매정보 수집에서 그대로 쓴다.
`PROJECT.md` 의 `market/` 설명에 '종목 마스터' 를 덧붙였다.
