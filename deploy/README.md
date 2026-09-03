# 배포

systemd 유닛 파일. 서버에서 아래로 설치한다.

```bash
sudo cp deploy/stock-portal-daily.service deploy/stock-portal-daily.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stock-portal-daily.timer
```

확인.

```bash
systemctl list-timers stock-portal-daily.timer
sudo systemctl start stock-portal-daily.service   # 즉시 1회 실행
tail -f logs/daily.log
```

`.env` 는 `WorkingDirectory` 에서 읽으므로 유닛에 비밀값을 쓰지 않는다.

## 일 1회 갱신 배치

`collectors.market.daily` 는 `price_daily` 에 빠진 최근 거래일을 찾아 채운다.
'오늘' 을 받지 않으므로 실행 시각에 민감하지 않고, 공개가 늦어지면 다음 날
자동으로 따라잡는다. 특정 날짜만 다시 받으려면 인자로 넘긴다.

```bash
.venv/bin/python -m collectors.market.daily 2026-08-26
```

미적재 거래일이 `config/collect.yaml` 의 `max_delay_days` 를 넘게 쌓이면
텔레그램으로 알린다. 조용히 아무것도 하지 않는 상태를 막기 위한 것이다.

같은 유닛에서 국면 판정(`collectors.market.regime`)이 이어 돈다.
쓸 수 있는 지표가 없으면 스스로 보류하므로 지표 수집기가 붙기 전에도 안전하다.
국면이 바뀔 때만 텔레그램으로 알린다.

### 종목 플래그가 맨 끝인 이유 (2026-09-01)

`stock_flags` 는 **반드시 일봉 뒤에 돈다.** 앞에서 돌면 일봉이 영영 안 쌓인다.

`stock_flags` 는 관리종목·거래정지가 바뀌면 `stock_status` 에 **오늘 날짜로
새 구간을 연다.** 그러면 `MAX(valid_from)` 이 오늘이 된다.

`stock_status` 에는 "시간을 되돌리지 않는다" 가드가 있다. 과거 날짜로 돌리면
그 뒤에 상장한 종목이 폐지로 잡히기 때문이다. **정당한 가드다.**

그런데 **KRX 는 D일 데이터를 D+1 에 공개한다.** `daily` 는 늘 *어제*를
채우므로, 도장이 오늘로 찍혀 있으면 어제로 못 돌아가고 매일 실패한다.

원래 16:10 키움 유닛에 있었고 8월 31일 첫 발화부터 이 문제가 났다.
이틀치 일봉이 통째로 빠졌다.

## 키움 수집 (분봉·수급)

```bash
sudo cp deploy/stock-portal-kiwoom.service deploy/stock-portal-kiwoom.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stock-portal-kiwoom.timer
```

일 1회 갱신과 타이머를 나눈 이유는 **출처가 다르기 때문**이다.
KRX 일봉은 다음 날 공개되지만 키움 분봉·수급은 당일 바로 받을 수 있다.
한 타이머에 묶으면 하루 늦어진다.

분봉과 수급은 한 유닛에 넣었다. 출처와 실행 시각이 같고 서로 의존하지 않는다.

**종목 플래그는 2026-09-01 에 이 유닛에서 뺐다.** 위 "종목 플래그가 맨 끝인
이유" 참조.
`ExecStart=-` 로 앞이 실패해도 뒤가 돈다.

**파티션 생성이 맨 앞이다.** `price_minute` 는 월 파티션이 없으면 적재가
실패한다. 여기만 `-` 를 붙이지 않아서, 실패하면 뒤가 돌지 않는다.

관심종목 200개 기준 분봉 약 4분, 수급 약 4분이다. 키움 유량이 1 이라
종목당 1초가 하한이다.

## 코드를 받은 뒤

**상시 프로세스는 `git pull` 만으로 바뀌지 않는다.** gunicorn 도 수집기도
시작할 때 읽은 코드로 계속 돈다.

```bash
git pull
sudo systemctl restart stock-portal-web.service stock-portal-news.service
```

설정 파일(`config/`)도 마찬가지다. 재시작하지 않으면 새 설정을 옛 코드가
읽어 오류가 난다 (2026-08-30 에 겪었다).

타이머로 도는 배치는 매번 새로 뜨므로 재시작이 필요 없다.

## 포털 웹 서버

상시 프로세스다. 타이머가 아니라 `multi-user.target` 에 물린다.

**포트는 8001 이다.** 문서가 적어둔 8000 은 무한매수 컨테이너가 쓰고 있다.
`sudo ss -ltnp` 로 확인하고 정했다 (2026-08-29).

```bash
sudo cp deploy/stock-portal-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stock-portal-web.service
curl -s localhost:8001/api/processes | head
```

Flask 와 gunicorn 이 새로 필요하다. 먼저 설치한다.

```bash
.venv/bin/pip install -r requirements.txt
```

### Nginx

```bash
sudo cp deploy/nginx-stock-portal.conf /etc/nginx/sites-available/stock-portal
sudo ln -sf /etc/nginx/sites-available/stock-portal /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

휴대폰에서 Tailscale 주소로 접속한다. `http://<tailscale-ip>/` 이다.

### 화면과 API

| 경로 | 내용 |
|---|---|
| `/` | 대시보드 (국면·지표·프로세스. 매매 항목은 Phase 8 부터) |
| `/market` | 시장분석 (지표 8종, 국면 판정 이력) |
| `/news` | 정보수집 (키워드 급등, 원문, 동의어 병합) |
| `/ops` | 운영·로그 (프로세스 상태, 최근 에러) |
| `/api/…` | 조회 API (`INTERFACES.md` 10장) |

**쓰기는 `POST /api/keywords/merge` 하나뿐이다.** 사전을 다듬는 동작이고,
파라미터를 바꾸는 엔드포인트는 만들지 않는다 (PROJECT.md 8.2).
엔진 제어는 Phase 8 에 붙는다.

프로세스 상태는 `heartbeat` 표를 읽는다. 표시할 프로세스와 '멈춤' 으로
볼 시간은 `config/portal.yaml` 에 있다. 목록에 없는 프로세스도 신호가
있으면 화면에 나온다.

## 텔레그램 수집 (Phase 5)

상시 프로세스다. 먼저 `.env` 에 `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` 를 넣고
**세션을 만든다.** 전화번호로 코드를 받는 절차라 사람이 직접 한다.

```bash
.venv/bin/python -m collectors.news.login
```

찍히는 채널 목록에서 수집할 채널의 숫자 ID 를 `config/sources.yaml` 에 넣고
적재한다.

```bash
.venv/bin/python -m common.db.seed
```

그 다음 서비스를 올린다.

```bash
sudo cp deploy/stock-portal-news.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stock-portal-news.service
tail -f logs/news.log
```

**세션이 없거나 만료되면 서비스가 뜨지 않는다.** 로그인을 물어야 하는데
systemd 아래에서는 물을 곳이 없기 때문이다. 5분에 5번 실패하면 유닛이
`failed` 로 멈춘다. 그때는 `login` 을 다시 실행하고 서비스를 재시작한다.

살아 있는지는 포털 운영·로그 탭에서 본다. 60초마다 신호를 남기므로
10분 넘게 조용하면 '멈춤' 으로 표시된다.

### 분석·집계 배치

10분마다 두 가지를 돌린다. 원문에서 종목·키워드를 뽑고(사전 다음에 LLM),
그 결과를 날짜별로 집계해 급등을 알린다.

```bash
sudo cp deploy/stock-portal-analyze.service deploy/stock-portal-analyze.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stock-portal-analyze.timer
```

사전을 고친 뒤 다시 분석하려면 표시를 지우고 배치를 돌린다.
원문은 지우지 않으므로 몇 번이든 다시 할 수 있다.

```sql
UPDATE raw_message SET analyzed_at = NULL, analysis_method = NULL;
```

과거분을 한꺼번에 다시 집계하려면 시작일을 준다.

```bash
.venv/bin/python -m collectors.news.aggregate 2026-08-14
```

LLM 비용은 `api_usage` 에 쌓인다. 일일 상한은 `config/limits.yaml` 이고,
넘으면 그날은 호출하지 않고 텔레그램으로 알린다. 분석되지 않은 원문은
표시가 비어 있어 다음 날 이어서 처리된다.

## 스윙 매매 엔진

24시간 상주한다. **장중에 재시작하지 않는다** — 배포는 15:40 이후다
(`CLAUDE.md` 배포).

```bash
sudo cp deploy/stock-portal-swing.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stock-portal-swing.service
tail -f logs/swing.log
```

내부 시각표로 하루를 돈다 (`config/engine.yaml`).

| 시각 | 하는 일 |
|---|---|
| 08:30 | 전날 계획을 동시호가 시장가로 제출 |
| 09:00~15:30 | 체결 추적(60초 간격), `command` 폴링(10초) |
| 15:30 | 미체결 잔량 취소 |
| 15:40 | `daily_pnl` 스냅샷 |
| 19:00 | 일봉 확인 → `scan`·`manage` → 다음 날 계획을 `signal` 에 기록 |

굴릴 계좌는 `config/engine.yaml` 의 `account_id` 이고, `accounts.yaml` 에서
`is_active` 가 아니면 **시작하지 않는다.** 실계좌가 이 경로로 걸린다.

19:00 에 일봉이 아직 없으면 20분 간격으로 여섯 번까지 다시 본다. 그래도
없으면 **그날 판단을 건너뛴다.** '데이터가 없어서 안 샀다' 는 안전한
실패다.

멈추거나 진입을 막으려면 `command` 테이블에 넣는다. 엔진이 10초마다
폴링한다. 화면 버튼은 5단계에서 붙는다.

```sql
INSERT INTO command (target, action, issued_by) VALUES ('engine-swing', 'halt_entry', 'manual');
```

## 장중 실측 (2026-09-01~03, 끝남)

사흘간 모의투자로 장중에만 잴 수 있는 것을 쟀다. **도구와 유닛은 지웠다.**
결과는 `docs/INTERFACES.md` 2.4 와 `context-notes.md` 2026-09-01~09-03 에 있다.

잰 것 — 슬리피지, 15:30 취소 가능 여부, 취소된 주문의 행방, 주문번호 필드,
매도 경로, 수수료·세금, 부분체결 수량 판정, 미체결 `ord_stt`.

**엔진 시간표 두 값이 이 실측으로 바뀌었다** (`config/engine.yaml`).
09:00 제출(동시호가 불가), 15:10 취소(15:30 이후 불가).

서버에 유닛이 남아 있으면 지운다.

```bash
sudo systemctl disable --now stock-portal-testorder.timer
sudo rm -f /etc/systemd/system/stock-portal-testorder.{service,timer}
sudo systemctl daemon-reload
```
