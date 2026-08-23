# SCHEMA.md — DB 스키마

> PostgreSQL 기준. `PROJECT.md`의 설계 원칙 8.4(멀티마켓), 8.6(이력)을 반영한다.
> 모든 프로세스가 이 스키마를 계약으로 삼는다. 컬럼명을 임의로 바꾸지 않는다.

---

## 0. 공통 규칙

### 데이터베이스 분리

| DB | 용도 |
|---|---|
| `portal_db` | 본 문서의 모든 테이블 |
| `lab_db` | 리치프랜즈랩. 별도 관리, 본 문서 범위 밖 |

같은 PostgreSQL 인스턴스 안에서 데이터베이스만 분리한다.

### 시간 표현

두 가지를 구분한다. 혼용하지 않는다.

| 종류 | 타입 | 용도 |
|---|---|---|
| **시각** | `TIMESTAMPTZ` | UTC 저장. 주문 시각, 수신 시각 등 |
| **거래일** | `DATE` | 시장 현지 기준. 일봉, 지표, 국면 등 |

거래일은 UTC로 바꾸지 않는다. "2026-08-24 한국 거래일"은 그 자체로 의미가 있다.

### 종목 식별자

`stock_id` 하나로 통일한다. 형식은 `{market}:{code}`.

```
KRX:005930      삼성전자
KOSDAQ:247540   에코프로비엠
NASDAQ:AAPL     (향후)
```

`market`, `code`는 조회 편의를 위해 별도 컬럼으로도 보관한다.

### 금액

`NUMERIC(20,4)` 사용. `currency` 컬럼을 함께 둔다.
국내만 쓰는 현재도 예외 없이 적용한다. 향후 환율 계산이 들어갈 자리다.

### 명명

- 테이블: 단수형 snake_case (`stock`, `order_request`)
- 생성 시각: `created_at`, 수정 시각: `updated_at`
- 예약어 회피: `order`는 SQL 예약어이므로 `order_request` 사용

---

## 1. 기준 정보

### market

거래시간을 코드에 하드코딩하지 않기 위한 테이블.

```sql
CREATE TABLE market (
    market          TEXT PRIMARY KEY,        -- 'KRX', 'KOSDAQ', 'NASDAQ'
    name            TEXT NOT NULL,
    country         TEXT NOT NULL,           -- 'KR', 'US'
    currency        TEXT NOT NULL,           -- 'KRW', 'USD'
    timezone        TEXT NOT NULL,           -- 'Asia/Seoul'
    open_time       TIME NOT NULL,           -- 09:00
    close_time      TIME NOT NULL,           -- 15:30
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);
```

초기 데이터는 `KRX`, `KOSDAQ` 두 건. 미국은 나중에 행만 추가한다.

### market_holiday

```sql
CREATE TABLE market_holiday (
    market          TEXT NOT NULL REFERENCES market(market),
    holiday_date    DATE NOT NULL,
    name            TEXT,
    PRIMARY KEY (market, holiday_date)
);
```

### stock

```sql
CREATE TABLE stock (
    stock_id        TEXT PRIMARY KEY,        -- 'KRX:005930'
    market          TEXT NOT NULL REFERENCES market(market),
    code            TEXT NOT NULL,           -- '005930'
    name            TEXT NOT NULL,
    sector          TEXT,
    listed_shares   BIGINT,                  -- 상장주식수
    is_managed      BOOLEAN DEFAULT FALSE,   -- 관리종목
    is_suspended    BOOLEAN DEFAULT FALSE,   -- 거래정지
    is_spac         BOOLEAN DEFAULT FALSE,
    is_preferred    BOOLEAN DEFAULT FALSE,   -- 우선주
    delisted_at     DATE,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_stock_code ON stock(code);
CREATE INDEX idx_stock_name ON stock(name);
```

`is_managed` 등 상태 플래그는 유니버스 필터에서 사용한다. 일 1회 갱신.

### account

```sql
CREATE TABLE account (
    account_id      TEXT PRIMARY KEY,        -- 'daytrade', 'swing', 'paper'
    broker          TEXT NOT NULL,           -- 'kiwoom'
    account_no      TEXT NOT NULL,           -- 실제 계좌번호
    strategy        TEXT NOT NULL,           -- 'daytrade' | 'swing'
    is_paper        BOOLEAN NOT NULL DEFAULT FALSE,
    currency        TEXT NOT NULL DEFAULT 'KRW',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);
```

계좌번호 자체는 DB에 두되, API 키·시크릿은 DB에 저장하지 않는다. `.env` 참조.

---

## 2. 시세 데이터

### price_daily

```sql
CREATE TABLE price_daily (
    stock_id        TEXT NOT NULL REFERENCES stock(stock_id),
    trade_date      DATE NOT NULL,
    open            NUMERIC(20,4) NOT NULL,
    high            NUMERIC(20,4) NOT NULL,
    low             NUMERIC(20,4) NOT NULL,
    close           NUMERIC(20,4) NOT NULL,
    volume          BIGINT NOT NULL,
    value           NUMERIC(20,0),           -- 거래대금
    PRIMARY KEY (stock_id, trade_date)
);
```

### price_minute

```sql
CREATE TABLE price_minute (
    stock_id        TEXT NOT NULL REFERENCES stock(stock_id),
    ts              TIMESTAMPTZ NOT NULL,    -- UTC
    open            NUMERIC(20,4) NOT NULL,
    high            NUMERIC(20,4) NOT NULL,
    low             NUMERIC(20,4) NOT NULL,
    close           NUMERIC(20,4) NOT NULL,
    volume          BIGINT NOT NULL,
    PRIMARY KEY (stock_id, ts)
);
```

**월 단위 파티셔닝을 적용한다.** 전 종목 1분봉은 연 30GB 수준이라 단일 테이블로
두면 조회가 느려진다. 백테스트가 특정 기간만 읽는 패턴이므로 파티션 효과가 크다.

### 수급

외국인 순매수 지표의 원천.

```sql
CREATE TABLE trading_flow (
    stock_id        TEXT NOT NULL REFERENCES stock(stock_id),
    trade_date      DATE NOT NULL,
    foreign_net     NUMERIC(20,0),           -- 외국인 순매수 (금액)
    institution_net NUMERIC(20,0),
    individual_net  NUMERIC(20,0),
    PRIMARY KEY (stock_id, trade_date)
);
```

시장 전체 집계는 이 테이블에서 합산하거나, 별도 지표로 `indicator_value`에 넣는다.

---

## 3. 정보수집

### source

수집 소스. 텔레그램 채널을 추가하면 여기에 행이 늘어난다.

```sql
CREATE TABLE source (
    source_id       SERIAL PRIMARY KEY,
    kind            TEXT NOT NULL,           -- 'telegram' | 'dart'
    identifier      TEXT NOT NULL,           -- 채널 ID 등
    name            TEXT NOT NULL,
    weight          NUMERIC(4,2) DEFAULT 1.0,  -- 신뢰도 가중치
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    last_success_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (kind, identifier)
);
```

`weight`는 채널마다 신뢰도가 다른 문제에 대응한다. 초기값은 전부 1.0.

### raw_message

원문 보관. 프롬프트 개선 후 재분석에 사용한다.

```sql
CREATE TABLE raw_message (
    message_id      BIGSERIAL PRIMARY KEY,
    source_id       INT NOT NULL REFERENCES source(source_id),
    external_id     TEXT,                    -- 원본 시스템의 ID
    content         TEXT NOT NULL,
    content_hash    TEXT NOT NULL,           -- 중복 제거용
    published_at    TIMESTAMPTZ NOT NULL,
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_filtered     BOOLEAN DEFAULT FALSE,   -- 규칙 필터에서 제외됨
    analyzed_at     TIMESTAMPTZ,             -- NULL이면 미분석
    analysis_method TEXT,                    -- 'dict' | 'llm'
    UNIQUE (source_id, content_hash)
);

CREATE INDEX idx_raw_published ON raw_message(published_at DESC);
CREATE INDEX idx_raw_unanalyzed ON raw_message(analyzed_at) WHERE analyzed_at IS NULL;
```

`analyzed_at IS NULL`인 행을 배치로 모아 LLM에 보낸다.

### keyword

키워드 사전. 동의어를 하나로 묶는다.

```sql
CREATE TABLE keyword (
    keyword_id      SERIAL PRIMARY KEY,
    term            TEXT NOT NULL UNIQUE,    -- '글라스기판'
    canonical_id    INT REFERENCES keyword(keyword_id),  -- '유리기판'을 가리킴
    category        TEXT,                    -- 'industry' | 'tech' | 'theme'
    is_confirmed    BOOLEAN DEFAULT FALSE,   -- 사용자가 확인한 항목
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

`canonical_id`가 NULL이면 자기 자신이 대표어다.
LLM이 새 표현을 만들어내면 `is_confirmed = FALSE`로 들어오고, 사용자가 화면에서
기존 키워드에 병합하거나 승인한다. 사전이 커질수록 LLM 호출이 줄어든다.

### keyword_mention

```sql
CREATE TABLE keyword_mention (
    message_id      BIGINT NOT NULL REFERENCES raw_message(message_id),
    keyword_id      INT NOT NULL REFERENCES keyword(keyword_id),
    PRIMARY KEY (message_id, keyword_id)
);
```

### keyword_daily

빈도 집계. 급등도 계산의 기준이 되는 이력.

```sql
CREATE TABLE keyword_daily (
    keyword_id      INT NOT NULL REFERENCES keyword(keyword_id),
    trade_date      DATE NOT NULL,
    mention_count   INT NOT NULL,
    weighted_count  NUMERIC(10,2),           -- source.weight 반영
    ma7             NUMERIC(10,2),           -- 7일 평균
    surge_ratio     NUMERIC(10,2),           -- mention_count / ma7
    PRIMARY KEY (keyword_id, trade_date)
);

CREATE INDEX idx_keyword_surge ON keyword_daily(trade_date, surge_ratio DESC);
```

**절대 빈도가 아니라 `surge_ratio`가 신호다.** "반도체 120회"는 정보가 아니고,
"유리기판 평소 4회 → 오늘 30회"가 정보다.

### stock_mention

```sql
CREATE TABLE stock_mention (
    message_id      BIGINT NOT NULL REFERENCES raw_message(message_id),
    stock_id        TEXT NOT NULL REFERENCES stock(stock_id),
    PRIMARY KEY (message_id, stock_id)
);
```

종목명 추출은 사전 매칭으로 처리한다. LLM을 쓰지 않는다.

### dart_disclosure

DART 공시는 구조가 정형이라 별도 테이블로 둔다.

```sql
CREATE TABLE dart_disclosure (
    rcept_no        TEXT PRIMARY KEY,        -- 접수번호
    stock_id        TEXT REFERENCES stock(stock_id),
    corp_name       TEXT NOT NULL,
    report_name     TEXT NOT NULL,
    disclosure_type TEXT,                    -- 공시 유형 코드
    submitted_at    TIMESTAMPTZ NOT NULL,
    url             TEXT,
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

공시 유형 코드가 이미 분류를 제공하므로 LLM 분류가 불필요하다.

---

## 4. 시장분석

### indicator

지표 정의. 8종으로 시작하되 행 추가로 확장 가능하다.

```sql
CREATE TABLE indicator (
    indicator_code  TEXT PRIMARY KEY,        -- 'DEPOSIT', 'CREDIT_BALANCE'
    name            TEXT NOT NULL,
    layer           TEXT NOT NULL,           -- 'sentiment' | 'risk' | 'position' | 'fundamental'
    frequency       TEXT NOT NULL,           -- 'daily' | 'monthly'
    source          TEXT NOT NULL,           -- 'kofia' | 'krx' | 'customs' | 'ecos'
    unit            TEXT,
    use_in_regime   BOOLEAN NOT NULL DEFAULT TRUE,  -- 판정에 사용 여부
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);
```

`use_in_regime = FALSE`로 두면 화면에는 보이되 판정에서 제외된다.
금리·M2·가계부채를 나중에 참고용으로 추가할 때 이 플래그를 쓴다.

초기 8종:

| indicator_code | layer | frequency |
|---|---|---|
| `DEPOSIT` | sentiment | daily |
| `CREDIT_BALANCE` | sentiment | daily |
| `FOREIGN_NET` | sentiment | daily |
| `VKOSPI` | risk | daily |
| `USDKRW` | risk | daily |
| `KOSPI_MA200_GAP` | position | daily |
| `EXPORT_YOY` | fundamental | monthly |
| `EXPORT_SEMI_YOY` | fundamental | monthly |

### indicator_value

```sql
CREATE TABLE indicator_value (
    indicator_code  TEXT NOT NULL REFERENCES indicator(indicator_code),
    period_date     DATE NOT NULL,           -- 일간은 해당일, 월간은 월초
    value           NUMERIC(20,6) NOT NULL,
    change_rate     NUMERIC(10,4),           -- 전기 대비 변화율
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (indicator_code, period_date)
);
```

수집기가 소스별로 달라도 이 형식으로 통일해서 넣는다.
판정 엔진은 데이터 출처를 알지 못한다.

### market_regime

**판정 이력. 이 테이블이 나중에 규칙을 검증하는 근거가 된다.**

```sql
CREATE TABLE market_regime (
    trade_date      DATE PRIMARY KEY,
    regime          TEXT NOT NULL,           -- 'danger' | 'neutral' | 'safe'
    score           NUMERIC(6,3) NOT NULL,
    layer_scores    JSONB,                   -- 계층별 점수
    indicators      JSONB NOT NULL,          -- 판정 시점의 지표값 스냅샷
    rule_version    TEXT NOT NULL,           -- 적용된 규칙 파일 버전
    is_override     BOOLEAN NOT NULL DEFAULT FALSE,
    override_reason TEXT,
    kospi_return    NUMERIC(10,4),           -- 당일 KOSPI 등락 (익일 채움)
    kosdaq_return   NUMERIC(10,4),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

`kospi_return`은 판정 다음 날 채운다. 이것이 있어야 나중에 "위험 판정일의
실제 시장 결과가 어땠는가"를 검증할 수 있다.

`indicators` 스냅샷이 핵심이다. 나중에 지표를 바꾸거나 규칙을 수정해도
"그때 어떤 값으로 그렇게 판정했는지"가 남는다.

`rule_version`으로 규칙 변경 전후를 구분할 수 있다.

---

## 5. 매매

### signal

**전략의 내용에 의존하지 않는 구조.** 근거는 `payload`에 통째로 넣는다.

```sql
CREATE TABLE signal (
    signal_id       BIGSERIAL PRIMARY KEY,
    stock_id        TEXT NOT NULL REFERENCES stock(stock_id),
    strategy        TEXT NOT NULL,           -- 'daytrade' | 'swing'
    side            TEXT NOT NULL,           -- 'BUY' | 'SELL'
    strength        NUMERIC(5,2),            -- 0~100
    payload         JSONB,                   -- 전략별 근거
    regime_at       TEXT,                    -- 발생 시점 국면
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    consumed_at     TIMESTAMPTZ              -- 주문으로 이어진 시각
);

CREATE INDEX idx_signal_pending ON signal(strategy, created_at)
    WHERE consumed_at IS NULL;
```

엔진은 `stock_id`, `side`, `strength`만 보고 동작한다.
`payload`에 무엇이 들어가든 주문 로직은 바뀌지 않는다.

### order_request

```sql
CREATE TABLE order_request (
    order_id        BIGSERIAL PRIMARY KEY,
    account_id      TEXT NOT NULL REFERENCES account(account_id),
    stock_id        TEXT NOT NULL REFERENCES stock(stock_id),
    signal_id       BIGINT REFERENCES signal(signal_id),
    side            TEXT NOT NULL,           -- 'BUY' | 'SELL'
    order_type      TEXT NOT NULL,           -- 'LIMIT' | 'MARKET'
    quantity        INT NOT NULL,
    price           NUMERIC(20,4),           -- 시장가는 NULL
    currency        TEXT NOT NULL DEFAULT 'KRW',
    status          TEXT NOT NULL,           -- 'pending'|'submitted'|'partial'|'filled'|'cancelled'|'rejected'
    broker_order_no TEXT,                    -- 증권사 주문번호
    filled_qty      INT NOT NULL DEFAULT 0,
    avg_fill_price  NUMERIC(20,4),
    error_message   TEXT,
    is_manual       BOOLEAN NOT NULL DEFAULT FALSE,  -- 화면에서 수동 청산
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_order_account_date ON order_request(account_id, created_at DESC);
CREATE INDEX idx_order_open ON order_request(status)
    WHERE status IN ('pending','submitted','partial');
```

### execution

부분 체결에 대응한다. 하나의 주문이 여러 번 체결될 수 있다.

```sql
CREATE TABLE execution (
    execution_id    BIGSERIAL PRIMARY KEY,
    order_id        BIGINT NOT NULL REFERENCES order_request(order_id),
    quantity        INT NOT NULL,
    price           NUMERIC(20,4) NOT NULL,
    fee             NUMERIC(20,4) DEFAULT 0,
    tax             NUMERIC(20,4) DEFAULT 0,
    executed_at     TIMESTAMPTZ NOT NULL
);
```

**수수료와 세금을 반드시 기록한다.** 백테스트 검증 시 실제 비용과 대조해야 한다.

### position

```sql
CREATE TABLE position (
    account_id      TEXT NOT NULL REFERENCES account(account_id),
    stock_id        TEXT NOT NULL REFERENCES stock(stock_id),
    quantity        INT NOT NULL,
    avg_price       NUMERIC(20,4) NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'KRW',
    opened_at       TIMESTAMPTZ NOT NULL,
    synced_at       TIMESTAMPTZ NOT NULL,    -- 증권사 잔고와 대조한 시각
    PRIMARY KEY (account_id, stock_id)
);
```

계좌가 전략별로 분리돼 있으므로 이 테이블이 곧 전략별 포지션이다.

**증권사 잔고를 정본으로 삼는다.** 이 테이블은 캐시이며, 주기적으로 대조해서
불일치가 발견되면 증권사 값으로 덮어쓰고 경고를 남긴다.

### daily_pnl

일별 손익 스냅샷. 대시보드와 성과 분석에 사용한다.

```sql
CREATE TABLE daily_pnl (
    account_id      TEXT NOT NULL REFERENCES account(account_id),
    trade_date      DATE NOT NULL,
    deposit         NUMERIC(20,4),           -- 예수금
    eval_amount     NUMERIC(20,4),           -- 평가금액
    total_asset     NUMERIC(20,4),
    realized_pnl    NUMERIC(20,4),           -- 당일 실현손익
    unrealized_pnl  NUMERIC(20,4),           -- 평가손익
    trade_count     INT DEFAULT 0,
    PRIMARY KEY (account_id, trade_date)
);
```

단타는 `realized_pnl`, 스윙은 `unrealized_pnl`을 주 지표로 표시한다.

### stock_filter

제외·허용 목록.

```sql
CREATE TABLE stock_filter (
    filter_id       SERIAL PRIMARY KEY,
    stock_id        TEXT NOT NULL REFERENCES stock(stock_id),
    strategy        TEXT NOT NULL,           -- 'daytrade'|'swing'|'all'
    filter_type     TEXT NOT NULL,           -- 'block' | 'allow'
    reason          TEXT,
    until_date      DATE,                    -- NULL이면 무기한
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

`filter_type = 'allow'`는 화이트리스트 모드에서만 적용된다.
모드 전환은 `config/`에서 설정한다.

---

## 6. 운영

### heartbeat

엔진과 포털은 HTTP가 아니라 이 테이블로 통신한다.

```sql
CREATE TABLE heartbeat (
    process_name    TEXT PRIMARY KEY,        -- 'engine-daytrade' 등
    status          TEXT NOT NULL,           -- 'running'|'idle'|'stopping'|'error'
    last_beat_at    TIMESTAMPTZ NOT NULL,
    detail          JSONB,                   -- 처리 건수 등
    started_at      TIMESTAMPTZ
);
```

포털은 `NOW() - last_beat_at`이 임계값을 넘으면 죽은 것으로 판단한다.

### command

포털 → 엔진 제어. 엔진이 폴링한다.

```sql
CREATE TABLE command (
    command_id      BIGSERIAL PRIMARY KEY,
    target          TEXT NOT NULL,           -- 'engine-daytrade' | 'all'
    action          TEXT NOT NULL,           -- 'stop'|'halt_entry'|'liquidate_all'|'close_position'
    params          JSONB,
    status          TEXT NOT NULL DEFAULT 'pending',  -- 'pending'|'acked'|'done'|'failed'
    issued_by       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acked_at        TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    result          TEXT
);

CREATE INDEX idx_command_pending ON command(target, created_at)
    WHERE status = 'pending';
```

`halt_entry`(신규 진입 차단)와 `liquidate_all`(전량 청산)은 별개 명령이다.
화면에서도 버튼을 분리한다.

### engine_run

엔진 실행 이력. **당시 어떤 파라미터로 돌았는지**를 남긴다.

```sql
CREATE TABLE engine_run (
    run_id          BIGSERIAL PRIMARY KEY,
    process_name    TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL,
    stopped_at      TIMESTAMPTZ,
    config_snapshot JSONB NOT NULL,          -- 시작 시점 설정 전체
    exit_reason     TEXT
);
```

몇 달 뒤 "6월에는 왜 이렇게 동작했지"를 확인할 수 있다.
파라미터를 설정 파일로만 바꾸기로 한 원칙(8.2)과 짝을 이룬다.

### event_log

```sql
CREATE TABLE event_log (
    event_id        BIGSERIAL PRIMARY KEY,
    process_name    TEXT NOT NULL,
    level           TEXT NOT NULL,           -- 'INFO'|'WARN'|'ERROR'|'CRITICAL'
    category        TEXT,                    -- 'order'|'collect'|'regime'|'system'
    message         TEXT NOT NULL,
    detail          JSONB,
    notified        BOOLEAN DEFAULT FALSE,   -- 텔레그램 발송 여부
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_event_recent ON event_log(created_at DESC);
CREATE INDEX idx_event_error ON event_log(level, created_at DESC)
    WHERE level IN ('ERROR','CRITICAL');
```

### api_usage

호출량과 비용 추적. 일일 상한 판단에 사용한다.

```sql
CREATE TABLE api_usage (
    usage_date      DATE NOT NULL,
    provider        TEXT NOT NULL,           -- 'kiwoom' | 'anthropic' | 'dart'
    endpoint        TEXT NOT NULL,           -- 모델명 또는 엔드포인트
    call_count      INT NOT NULL DEFAULT 0,
    input_tokens    BIGINT DEFAULT 0,
    output_tokens   BIGINT DEFAULT 0,
    cost_usd        NUMERIC(12,6) DEFAULT 0,
    PRIMARY KEY (usage_date, provider, endpoint)
);
```

`collector-news`는 매 호출 후 이 테이블을 갱신하고, 일일 상한 초과 시 중단한다.

---

## 7. 백테스트

### backtest_run

```sql
CREATE TABLE backtest_run (
    run_id          BIGSERIAL PRIMARY KEY,
    strategy        TEXT NOT NULL,
    from_date       DATE NOT NULL,
    to_date         DATE NOT NULL,
    universe        TEXT,                    -- 대상 종목군 설명
    params          JSONB NOT NULL,          -- 사용한 파라미터 전체
    initial_capital NUMERIC(20,4) NOT NULL,
    final_capital   NUMERIC(20,4),
    total_return    NUMERIC(10,4),
    mdd             NUMERIC(10,4),           -- 최대낙폭
    win_rate        NUMERIC(10,4),
    trade_count     INT,
    sharpe          NUMERIC(10,4),
    fee_rate        NUMERIC(10,6) NOT NULL,  -- 반영한 수수료율
    slippage_rate   NUMERIC(10,6) NOT NULL,  -- 반영한 슬리피지
    note            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

`fee_rate`와 `slippage_rate`를 NOT NULL로 둔다. 0으로 돌린 결과도 그 사실이 남는다.

### backtest_trade

```sql
CREATE TABLE backtest_trade (
    trade_id        BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES backtest_run(run_id) ON DELETE CASCADE,
    stock_id        TEXT NOT NULL,
    entry_at        TIMESTAMPTZ NOT NULL,
    entry_price     NUMERIC(20,4) NOT NULL,
    exit_at         TIMESTAMPTZ,
    exit_price      NUMERIC(20,4),
    quantity        INT NOT NULL,
    pnl             NUMERIC(20,4),
    pnl_rate        NUMERIC(10,4),
    exit_reason     TEXT,                    -- 'target'|'stop'|'timeout'|'signal'
    signal_payload  JSONB
);
```

---

## 8. 초기 데이터

구축 시 다음을 채운다.

| 테이블 | 내용 |
|---|---|
| `market` | KRX, KOSDAQ |
| `market_holiday` | 당해 연도 휴장일 |
| `stock` | 전 상장종목 (KRX 또는 DART 명단) |
| `account` | 단타·스윙·모의 3건 |
| `indicator` | 8종 |
| `source` | 텔레그램 채널, DART |

`keyword`는 비어 있는 상태로 시작한다. 운영하면서 채워진다.

---

## 9. 미결 사항

| 항목 | 비고 |
|---|---|
| `price_minute` 파티션 주기 | 월 단위 예정. 실제 증가 속도 보고 확정 |
| 백업 방식 | `pg_dump` 일 1회 예정. 보관 기간 미정 |
| `position` 대조 주기 | 장중 N분 간격 + 장 마감 후 1회 |
| 시장 전체 수급 지표 | `trading_flow` 합산 vs `indicator_value` 직접 저장 |
