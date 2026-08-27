# checklist.md — Phase 1 기반 구축 (2026-08-27 완료)

> `ROADMAP.md` Phase 1 기준. 완료 기준을 충족한 뒤 Phase 2로 넘어간다.

---

## 설계 결정

- [x] A-1 `stock_id` 접두어를 거래소로 정의
- [x] A-2 `client_order_id` (ULID, NOT NULL UNIQUE)
- [x] A-3 계좌번호는 `.env` 에만
- [x] A-4 `position.opened_at` NULL 허용
- [x] A-5 수정주가 (원주가 + `corporate_action` + `adj_factor`)
- [x] A-6 생존편향 (`listed_at`, `stock_status`, 행 삭제 금지)
- [ ] A-7 `index_price` 테이블 — Phase 3 전
- [ ] A-8 국면 layer 이름 통일 — Phase 3 전
- [ ] A-9 `indicator_value.is_final` — Phase 3 전
- [ ] A-10 `regime_override` 테이블 — Phase 3 전

## Phase 0 — 사전 준비

- [x] 키움 REST API 신청 / 모의투자 계좌 / 계좌 추가 개설
- [x] DART API 키 발급
- [x] 공인 IP 확인·등록 (값은 `.env` 의 `KIWOOM_ALLOWED_IP`)
- [ ] 토스증권 오픈API 신청 (보류 가능)
- [x] GitHub 프라이빗 저장소 생성 (`bhchoi-claude/stock-portal`)
- [x] **완료 기준 — 모의투자 계좌로 시세 조회 성공** (2026-08-25, ka10001)
- [ ] 실전 앱키 발급·IP 등록 — Phase 9 진입 전

## 마이그레이션

- [x] 디렉토리 구조 (`common/db/migrations/`)
- [x] 러너 (`common/db/migrate.py`) — status / apply, checksum 검사
- [x] `001_initial.sql` — 32개 테이블
- [x] `002_seed_static.sql` — `exchange` 1건, `indicator` 8종
- [x] sqlglot 파싱 검증, FK 순서·컬럼 중복·인덱스 대상 점검
- [x] 파티션 DDL 생성 결과 검증 (2026-08 ~ 2027-12, 17개)
- [x] **서버에서 실제 적용** (2026-08-24, PostgreSQL 18.6)
- [x] 테이블 50개 확인 (본체 32 + `schema_migration` + 파티션 17)
- [x] 시드 확인 — `exchange` 1건, `indicator` 8종

## 남은 Phase 1 작업

- [x] bh-server 24시간 운영 설정 (2026-08-26)
  - [x] 덮개 닫힘 무시 — `/etc/systemd/logind.conf.d/99-lid.conf`
  - [x] 절전 비활성 — sleep/suspend/hibernate/hybrid-sleep 마스킹
  - [x] **배터리 충전 상한 80%** — `lg-battery-care.service`
        LG 드라이버는 80 과 100 만 받는다. 60 은 거부된다
  - [x] 커널 자동 재부팅 비활성 — `/etc/apt/apt.conf.d/99-no-auto-reboot`
  - [ ] 공유기 DHCP 예약 — Tailscale 로 접속하므로 우선순위 낮음
  - [ ] BIOS 전원복구 자동부팅 — 정전 대비. 셸 밖 작업
- [x] PostgreSQL 설치, `portal_db` 생성 (18.6)
- [x] 서버에 venv 구성 (`.venv/`) — Ubuntu 26.04 는 PEP 668 로 시스템 설치를 막는다
- [ ] `.env` 나머지 키 채우기 (`DATABASE_URL`, 키움 모의 키는 완료)
  - [x] `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (2026-08-26)
  - [ ] `DART_API_KEY`, `ANTHROPIC_API_KEY`
  - [ ] `KIWOOM_ACCOUNT_*` 3건, `KIWOOM_ALLOWED_IP`
  - [ ] `KRX_API_KEY` — 오픈API 승인 후
- [x] KRX 오픈API 사용 신청 — **승인 완료** (2026-08-26)
- [ ] 기준 데이터 적재 스크립트 — **출처는 KRX 로 확정** (2026-08-26)
  - [x] `config/` 신설, `common/config.py` YAML 로더
  - [x] `account` — `python -m common.db.seed` (계좌번호는 넣지 않는다)
  - [x] `source` — DART 1건. 텔레그램 채널은 목록 확정 후 추가
  - [x] **서버에서 seed 실행** (2026-08-26) — 계좌 3건, 소스 1건. 39 passed
  - [x] 엔드포인트 확인 — `data-dbg.krx.co.kr/svc/apis/sto/{apiId}`, 헤더 `AUTH_KEY`
        `stk_isu_base_info` / `ksq_isu_base_info` / `knx_isu_base_info`
  - [x] **API 이용신청** (2026-08-27) — 종목기본정보 3종 + 일별매매정보
  - [x] 응답 실측 완료 (2026-08-27) — KOSPI 944 / KOSDAQ 1823 / KONEX 108
  - [x] `collectors/market/krx.py` 클라이언트
  - [x] `collectors/market/stock_master.py` — `stock` + `stock_status` 적재
  - [x] **서버에서 적재 실행** (2026-08-27) — 2849건. KOSPI 918 / KOSDAQ 1823 / KONEX 108
        KOSPI 는 944건 중 리츠·투자회사 26건을 제외했다
  - [~] `exchange_holiday` — **Phase 2 로 이관.** KRX 에 휴장일 API 가 없다
        일별매매정보의 거래일에서 역산한다
- [x] `common/db/` 모델 계층 — 기준 데이터 6종 + `event_log`
  - [x] `conn.py` 커넥션·트랜잭션, `load_database_url` 을 여기로 통합
  - [x] `models.py` dataclass 6종, `make_stock_id`
  - [x] `master.py` upsert·조회
  - [x] `events.py` `event_log` 기록
  - [ ] 나머지 26개 테이블 — 쓰는 시점에 추가한다. 미리 만들지 않는다
- [x] `common/notify/` 텔레그램 알림
  - [x] `base.py` `Notifier` 인터페이스
  - [x] `telegram.py` `TelegramNotifier` — 평문 발송, `ok` 판정, 토큰 비노출
  - [x] `python -m common.notify` 테스트 발송 CLI
  - [x] **서버에서 실제 발송 확인** (2026-08-26) — 개인 대화방 수신 성공
  - [ ] 중복 억제 (`event_log.notified`) — 발송 주체가 생기는 Phase 3 에서
- [x] `common/env.py` — `.env` 읽기를 한 곳으로 모음
- [~] Nginx + systemd 기본 설정 — **Phase 2 로 이관** (2026-08-26)
- [x] pytest 셋업 (`pytest.ini`, `conftest.py`, `requirements-dev.txt`)
  - [x] 스키마 드리프트 테스트 — DB 없이 돈다
  - [x] **DB 통합 테스트 서버에서 실행** (2026-08-26, 20 passed) — 로컬은 skip 된다
- [ ] 브로커 목(mock) — ROADMAP 에 없으나 Phase 2 에 필요

## Phase 1 완료 기준

- [x] 전 상장종목이 `stock` 테이블에 적재됨 (2026-08-27) — 2849건
- [x] 텔레그램으로 테스트 알림 수신 (2026-08-26)
- [x] 서버 24시간 운영 설정 완료 (2026-08-26) — 절전·덮개·자동재부팅·배터리 상한

## Phase 2 로 넘기는 항목

- [ ] `price_minute` 월 파티션 자동 생성 배치 (현재 2027-12 까지만 존재)
- [ ] `corporate_action` 수집 — 출처 확인 필요
- [ ] `adj_factor` 계산 배치
- [ ] 종목 상태 갱신이 `stock_status` 에 이력을 남기도록 구현
