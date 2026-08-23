# checklist.md — Phase 1 기반 구축

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

## 마이그레이션

- [x] 디렉토리 구조 (`common/db/migrations/`)
- [x] 러너 (`common/db/migrate.py`) — status / apply, checksum 검사
- [x] `001_initial.sql` — 32개 테이블
- [x] `002_seed_static.sql` — `exchange` 1건, `indicator` 8종
- [x] sqlglot 파싱 검증, FK 순서·컬럼 중복·인덱스 대상 점검
- [x] 파티션 DDL 생성 결과 검증 (2026-08 ~ 2027-12, 17개)
- [ ] **서버에서 실제 적용** — 로컬에 PostgreSQL 이 없어 미실행
- [ ] 적용 후 `\d` 로 테이블 32개·인덱스 확인

## 남은 Phase 1 작업

- [ ] bh-server 24시간 운영 설정
  - 덮개 닫힘 무시, 절전 비활성
  - **배터리 충전 임계값 60~80% 제한**
  - 커널 자동 재부팅 비활성
  - 공유기 DHCP 예약, BIOS 전원복구 자동부팅
- [ ] PostgreSQL 설치, `portal_db` 생성
- [ ] `.env` 작성 (`DATABASE_URL`, `KIWOOM_ACCOUNT_*` 등)
- [ ] 기준 데이터 적재 스크립트
  - [ ] `exchange_holiday` — 당해 연도 휴장일. 출처 미정
  - [ ] `stock` — 전 상장종목. KRX / DART 중 출처 미정
  - [ ] `stock_status` — 적재 시점 상태 1행씩
  - [ ] `account` — 3건 (계좌번호는 넣지 않는다)
  - [ ] `source` — 텔레그램 채널, DART
- [ ] `common/db/` 모델 계층
- [ ] `common/notify/` 텔레그램 알림
- [ ] Nginx + systemd 기본 설정
- [ ] pytest 셋업 + 브로커 목(mock) — ROADMAP 에 없으나 Phase 2 에 필요

## Phase 1 완료 기준

- [ ] 전 상장종목이 `stock` 테이블에 적재됨
- [ ] 텔레그램으로 테스트 알림 수신
- [ ] 서버 재부팅 후 서비스 자동 기동 확인

## Phase 2 로 넘기는 항목

- [ ] `price_minute` 월 파티션 자동 생성 배치 (현재 2027-12 까지만 존재)
- [ ] `corporate_action` 수집 — 출처 확인 필요
- [ ] `adj_factor` 계산 배치
- [ ] 종목 상태 갱신이 `stock_status` 에 이력을 남기도록 구현
