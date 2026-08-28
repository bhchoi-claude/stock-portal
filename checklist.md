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

## 출처 조사 결과 (2026-08-28)

**KRX 지수 API 이용신청은 필요 없다.** 키움에 다 있다.

| 지표 | 출처 | 신청 |
|---|---|---|
| `VKOSPI` | 키움 `ka20006` 업종코드 `603` | **불필요** |
| `KOSPI_MA200_GAP` | 키움 `ka20006` 업종코드 `001` | **불필요** |
| `kospi_return` | 위와 같음 | **불필요** |
| `FOREIGN_NET` | 키움 `ka10059` (이미 수집 중) | 불필요 |
| `DEPOSIT` / `CREDIT_BALANCE` | data.go.kr 금융위원회_금융투자협회종합통계정보 | 자동승인 |
| `EXPORT_YOY` / `EXPORT_SEMI_YOY` | data.go.kr 관세청_품목별 수출입실적(GW) | 개발단계 자동승인 |
| `USDKRW` | 한국은행 ECOS | 가입 시 자동 발급 |

## 사용자 조치 필요

- [ ] **data.go.kr 인증키** — 관세청·금융투자협회 두 API 활용신청. 둘 다 자동승인
- [ ] **ECOS 인증키** — ecos.bok.or.kr 회원가입. 1일 이내 사용 가능
- [ ] `DART_API_KEY` 를 서버 `.env` 에 넣기 — Phase 2 잔여분과 Phase 5 에 쓴다

## 확인이 남은 것

- 투자자예탁금이 금융투자협회종합통계의 '증시 자금 흐름' 에 있는지.
  신용공여 잔고는 있는 것이 확인됐다
- 수출 **20일 잠정치**는 월간 품목별 실적과 다른 자료다.
  `PROJECT.md` 는 20일 잠정치를 쓴다고 적혀 있다. 별도 확인이 필요하다

## 플러그인 구조 (막히지 않음)

- [x] `Collector` ABC + `CollectResult` / `IndicatorRecord` (2026-08-28)
- [x] 수집기 실행기 (2026-08-28) — `collectors/market/indicator_runner.py`
- [x] 실패를 `event_log` 에 남기고 `source.last_success_at` 을 갱신한다
- [x] 1시간 내 반복 실패 시 알림 (2026-08-28)
- [x] `indicator_value` 적재 + 전기 대비 `change_rate` 계산 (2026-08-28)

## 지표 수집기

- [x] `FOREIGN_NET` (2026-08-29) — 상장 2849종목 전체. 커버리지 98.9% 이상
- [ ] **임계값 재조정** — 지표 8종이 다 붙은 뒤 한 번에 한다 (승인 사항).
      지금은 85~99% 포화라 사실상 이진 신호다. 알고 두는 상태다.
      조정할 때 `regime_rules.yaml` 의 `version` 을 올린다
- [x] `VKOSPI` / `KOSPI_MA200_GAP` (2026-08-28) — 키움 `ka20006`. 254일치 적재
- [ ] `DEPOSIT` / `CREDIT_BALANCE` — data.go.kr 인증키 대기
- [ ] `USDKRW` — ECOS 인증키 대기
- [ ] `EXPORT_YOY` / `EXPORT_SEMI_YOY` — data.go.kr 인증키 대기

## 국면 판정

- [x] `config/regime_rules.yaml` (2026-08-28) — 임계값은 전부 임시값이다
- [x] `RegimeEngine` (2026-08-28) — 결측 정규화 테스트 포함
- [x] 일 1회 판정 → `market_regime` 적재 (2026-08-28) — 일 1회 유닛에 연결
- [x] 익일 `kospi_return` 채우기 (2026-08-28) — `kosdaq_return` 도 함께
- [x] 국면 전환 시 알림 (2026-08-28) — 같은 국면 유지 시 무음. 실검증

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
