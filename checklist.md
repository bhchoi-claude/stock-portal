# checklist.md — Phase 3 시장분석

> Phase 1·2 체크리스트는 커밋 `2227f15` 이전 이력에 있다.
> 완료 요약은 `docs/ROADMAP.md` 진행 현황 참조.

**주문이 나가지 않아 안전하다. 수집기 플러그인 구조를 여기서 검증한다.**

---

## 실측으로 확인된 것 (2026-08-28)

- `indicator` 8종은 이미 적재돼 있다. `indicator_value` 는 비어 있다
- **KRX 지수 API 3종(`idx/kospi_dd_trd` 등)은 미신청이다.** 401 이 온다.
  경로는 맞다 (401 JSON 이 왔다는 것이 그 증거다)
- 키움 `ka10059` 로 종목별 수급은 받고 있다. 상위 200종목뿐이다

## 막혀 있는 것 — 사용자 조치 필요

- [ ] **KRX 지수 API 이용신청 3종** — `VKOSPI`, `KOSPI_MA200_GAP`,
      `kospi_return` 이 전부 여기 달려 있다
- [ ] 금융투자협회 API — `DEPOSIT`, `CREDIT_BALANCE`
- [ ] 관세청 API — `EXPORT_YOY`, `EXPORT_SEMI_YOY`
- [ ] 환율 출처 확정 (ECOS 등) — `USDKRW`
- [ ] `DART_API_KEY` 를 서버 `.env` 에 넣기 — Phase 2 잔여분과 Phase 5 에 쓴다

## 플러그인 구조 (막히지 않음)

- [ ] `Collector` ABC + `CollectResult` / `IndicatorRecord` (`INTERFACES.md` 6장)
- [ ] 수집기 실행기 — 한 수집기의 예외가 다른 수집기로 번지지 않는다
- [ ] 실패를 `event_log` 에 남기고 `source.last_success_at` 을 갱신한다
- [ ] 1시간 내 반복 실패 시 알림
- [ ] `indicator_value` 적재 + 전기 대비 `change_rate` 계산

## 지표 수집기

- [ ] `FOREIGN_NET` — `trading_flow` 집계. **상위 200종목만 있어 시장 전체가
      아니다.** 전 종목으로 넓힐지 결정해야 한다 (`SCHEMA.md` 미결 사항)
- [ ] `VKOSPI` / `KOSPI_MA200_GAP` — KRX 지수 API. 이용신청 대기
- [ ] `DEPOSIT` / `CREDIT_BALANCE` — 금융투자협회. 출처 조사 필요
- [ ] `USDKRW` — 출처 조사 필요
- [ ] `EXPORT_YOY` / `EXPORT_SEMI_YOY` — 관세청. 출처 조사 필요

## 국면 판정

- [ ] `config/regime_rules.yaml` — 임계값은 전부 여기 둔다
- [ ] `RegimeEngine` — 지표 결측 시 정규화 (`CLAUDE.md` 필수 테스트)
- [ ] 일 1회 판정 → `market_regime` 적재
- [ ] 익일 `kospi_return` 채우기 — KRX 지수 API 대기
- [ ] 국면 전환 시 알림

## Phase 3 완료 기준

- [ ] 지표 8종이 매일 자동 수집됨
- [ ] 국면 판정이 매일 기록되고 이력이 쌓임
- [ ] 소스 하나를 강제로 실패시켜도 나머지가 정상 동작

---

## Phase 2 잔여 (사용자 조치)

- [ ] 키움 수집 타이머 등록 — `deploy/README.md` 참조. sudo 필요
- [ ] 재부팅 후 자동 기동 확인 — 서버 재부팅이 필요하다
- [ ] 인적분할 조정 / 무상·유상증자 구분 / 현금배당 — DART 키가 `.env` 에 들어가면

## 건드리지 않은 것

- 서버 `~/stock-portal/ion` — 정체불명 파일(398바이트, 8/25). 내 작업과 무관하다
- `docs/INTERFACES.md` 코드블록이 `ruff format` 기준에 걸린다. 기존부터 그랬다
