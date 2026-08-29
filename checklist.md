# checklist.md — Phase 4 포털 셸 (읽기 전용)

> Phase 2·3 체크리스트는 커밋 `c383c65` 이전 이력에 있다.
> 완료 요약은 `docs/ROADMAP.md` 진행 현황 참조.

---

## 시작 전에 정한 것 (2026-08-29)

사용자 확인을 받은 네 가지다. 이유는 `context-notes.md` 에 있다.

- 프레임워크는 **Flask**. 조회 API + 정적 화면이라 비동기가 필요 없다
- 화면은 **최소 HTML 을 직접 쓴다**. Claude Design 산출물이 나오면 템플릿만 바꾼다
- 프로세스 상태는 **`heartbeat` 테이블**로 간다. 쓰는 코드가 없어 이번에 만든다
- `heartbeat.process_name` 은 **모듈별**. `event_log` 의 `process_name` 과 같은 이름을 쓴다
- `GET /api/events` 를 추가한다. `INTERFACES.md` 10장 표에 없던 것이라 문서도 고친다

## heartbeat

- [x] `common/db/heartbeat.py` — upsert / 목록 조회
- [x] `run_with_heartbeat()` — 시작 `running`, 종료코드 0 이면 `idle`, 아니면 `error`
- [x] 수집기 7종 진입점에 연결 (`daily`, `indicators`, `regime`, `partitions`,
      `price_minute`, `trading_flow`, `stock_flags`)
- [x] 테스트 — 성공·실패·예외 세 경로가 각각 맞는 상태로 남는가

## 조회 함수 (`common/db/`)

- [x] `regime.py` — 현재 국면, 판정 이력
- [x] `indicators.py` — 지표 현황 스냅샷 (정의 + 최신값)
- [x] `events.py` — 최근 이벤트
- [x] `heartbeat.py` — 프로세스 상태

## 포털

- [x] `portal/` Flask 앱 (`create_app`)
- [x] `GET /api/regime/current`
- [x] `GET /api/regime/history?from=&to=`
- [x] `GET /api/indicators`
- [x] `GET /api/processes`
- [x] `GET /api/events?level=&limit=`
- [x] `GET /api/dashboard` — 매매 항목은 빈 채로 둔다 (Phase 8 이후)
- [x] `config/portal.yaml` — 프로세스 목록, 정지 판정 임계 시간, 조회 기본 구간

## 화면

- [x] 대시보드 (부분) — 국면, 지표 요약, 프로세스 상태
- [x] 시장분석 — 지표 8종, 국면 판정 이력
- [x] 운영·로그 (기초) — 프로세스 상태, 최근 에러
- [x] 모바일 폭에서 읽히는가 — 375px 표본 데이터로 확인. 두 번 고쳤다.
      지표는 값을 앞 열로 옮기고, 에러는 표를 버리고 두 줄 목록으로 바꿨다

## 배포

- [x] `deploy/stock-portal-web.service` — 상시 프로세스 (gunicorn)
- [x] `deploy/nginx-stock-portal.conf` — `/` → `portal:8000`
- [x] `deploy/README.md` 설치 절차

## 문서

- [x] `INTERFACES.md` 10장에 `/api/events` 추가
- [x] `ROADMAP.md` 진행 현황 표 갱신 — Phase 2·3 이 '미시작' 으로 남아 있었다

## Phase 4 완료 기준

- [ ] Tailscale 로 휴대폰에서 접속해 시장분석 화면이 보인다
- [ ] 수집기 상태가 화면에 반영된다

---

## 사용자 조치 필요

- [x] 서버에 `pip install -r requirements.txt` (2026-08-29) — 전체 265건 통과
- [x] `stock-portal-web.service` 등록 (2026-08-29)
- [ ] 유닛 갱신 — 포트를 8001 로 고쳤다. 서버에 다시 복사해야 한다
- [ ] Nginx 설치·설정
- [ ] 휴대폰에서 Tailscale 접속 확인

## 08-31 에 확인할 것 (Phase 2 에서 이월)

설정상으로는 동작한다. 첫 자동 실행을 아직 못 봤을 뿐이다.

- [ ] 분봉 자동 적재 — 키움 타이머 첫 발화 16:10. 지금까지는 손으로 돌렸다
- [ ] `stock_flags` 플래그 생존 — 데일리 19:00 에 KRX 마스터가 돈 뒤에도
      관리종목·거래정지가 남는지. DB 테스트로는 확인했다

## 남은 것 (다른 단계로 넘김)

- [ ] `KOSPI_MA200_GAP` 도메인 검토 — 이격도 67% 가 '안전' 이 된다.
      과열과 과랭을 함께 위험으로 보려면 선형이 아닌 규칙이 필요하다.
      전략 판단이라 임의로 정하지 않는다 (Phase 7)
- [ ] 현금배당 수집 — `alotMatter` 로 가능하다. `adjusts_price=FALSE` 라
      조정계수에는 영향 없다

## 건드리지 않은 것

- 서버 `~/stock-portal/ion` — 정체불명 파일(398바이트, 8/25). 내 작업과 무관하다
- `docs/INTERFACES.md` 코드블록이 `ruff format` 기준에 걸린다. 기존부터 그랬다
