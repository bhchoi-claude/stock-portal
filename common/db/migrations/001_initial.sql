-- 초기 스키마. SCHEMA.md 기준. A-1~A-6 결정 반영.
--
-- 반영된 결정
--   A-1  stock_id 접두어는 거래소(exchange). market -> exchange 로 rename,
--        stock.exchange(불변) + stock.board(가변) 분리
--   A-2  order_request.client_order_id (ULID, NOT NULL UNIQUE)
--   A-3  account.account_no 없음. 계좌번호는 .env 에만 둔다
--   A-4  position.opened_at NULL 허용
--   A-5  price_daily.adj_factor + corporate_action
--   A-6  stock.listed_at + stock_status 변경 이력
--
-- Phase 3 결정 대기 항목(index_price, indicator_value.is_final, regime_override)은
-- 확정 후 별도 마이그레이션으로 추가한다.

-- =====================================================================
-- 1. 기준 정보
-- =====================================================================

CREATE TABLE exchange (
    exchange        TEXT PRIMARY KEY,        -- 'KRX', 'NASDAQ'
    name            TEXT NOT NULL,
    country         TEXT NOT NULL,           -- 'KR', 'US'
    currency        TEXT NOT NULL,           -- 'KRW', 'USD'
    timezone        TEXT NOT NULL,           -- 'Asia/Seoul'
    open_time       TIME NOT NULL,
    close_time      TIME NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE exchange_holiday (
    exchange        TEXT NOT NULL REFERENCES exchange(exchange),
    holiday_date    DATE NOT NULL,
    name            TEXT,
    PRIMARY KEY (exchange, holiday_date)
);

CREATE TABLE stock (
    stock_id        TEXT PRIMARY KEY,        -- 'KRX:005930'
    exchange        TEXT NOT NULL REFERENCES exchange(exchange),
    code            TEXT NOT NULL,           -- '005930'
    board           TEXT NOT NULL,           -- 'KOSPI' | 'KOSDAQ' | 'KONEX'. 현재값 캐시
    name            TEXT NOT NULL,
    sector          TEXT,
    listed_shares   BIGINT,
    is_managed      BOOLEAN DEFAULT FALSE,   -- 관리종목. 현재값 캐시
    is_suspended    BOOLEAN DEFAULT FALSE,   -- 거래정지. 현재값 캐시
    is_spac         BOOLEAN DEFAULT FALSE,
    is_preferred    BOOLEAN DEFAULT FALSE,
    listed_at       DATE,                    -- 상장일
    delisted_at     DATE,                    -- 상장폐지일. 행은 삭제하지 않는다
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_stock_code ON stock(code);
CREATE INDEX idx_stock_name ON stock(name);

-- 종목 상태의 변경 이력. 시점 조회의 정본.
-- 상태가 바뀐 날만 행을 추가한다. 매일 스냅샷을 쌓지 않는다.
CREATE TABLE stock_status (
    stock_id        TEXT NOT NULL REFERENCES stock(stock_id),
    valid_from      DATE NOT NULL,
    valid_to        DATE,                    -- NULL이면 현재까지 유효
    board           TEXT NOT NULL,
    is_managed      BOOLEAN NOT NULL DEFAULT FALSE,
    is_suspended    BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (stock_id, valid_from)
);

-- 계좌번호는 저장하지 않는다. account_id 로 .env 의 환경변수를 찾는다. (A-3)
CREATE TABLE account (
    account_id      TEXT PRIMARY KEY,        -- 'daytrade', 'swing', 'paper'
    broker          TEXT NOT NULL,           -- 'kiwoom'
    strategy        TEXT NOT NULL,           -- 'daytrade' | 'swing'
    is_paper        BOOLEAN NOT NULL DEFAULT FALSE,
    currency        TEXT NOT NULL DEFAULT 'KRW',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

-- =====================================================================
-- 2. 시세 데이터
-- =====================================================================

-- 저장하는 값은 원주가다. 한 번 쓰면 바뀌지 않는다.
-- 조정가 = close * adj_factor, 조정거래량 = volume / adj_factor
CREATE TABLE price_daily (
    stock_id        TEXT NOT NULL REFERENCES stock(stock_id),
    trade_date      DATE NOT NULL,
    open            NUMERIC(20,4) NOT NULL,
    high            NUMERIC(20,4) NOT NULL,
    low             NUMERIC(20,4) NOT NULL,
    close           NUMERIC(20,4) NOT NULL,
    volume          BIGINT NOT NULL,
    value           NUMERIC(20,0),           -- 거래대금
    adj_factor      NUMERIC(20,10) NOT NULL DEFAULT 1,  -- corporate_action 에서 계산한 파생값
    PRIMARY KEY (stock_id, trade_date)
);

-- adj_factor 계산의 정본. 이벤트를 뒤늦게 발견해도 시세는 건드리지 않는다.
CREATE TABLE corporate_action (
    action_id       BIGSERIAL PRIMARY KEY,
    stock_id        TEXT NOT NULL REFERENCES stock(stock_id),
    effective_date  DATE NOT NULL,           -- 적용일 (권리락일 기준)
    action_type     TEXT NOT NULL,           -- 'split'|'merge'|'bonus'|'rights'|'dividend'
    ratio           NUMERIC(20,10),
    adjusts_price   BOOLEAN NOT NULL,        -- split/merge/bonus 만 TRUE
    source          TEXT,                    -- 'dart'|'kiwoom'|'krx'|'manual'
    detail          JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (stock_id, effective_date, action_type)
);

CREATE INDEX idx_corp_action_stock ON corporate_action(stock_id, effective_date);

-- 월 단위 파티셔닝. 분봉에는 조정계수를 두지 않는다.
CREATE TABLE price_minute (
    stock_id        TEXT NOT NULL REFERENCES stock(stock_id),
    ts              TIMESTAMPTZ NOT NULL,    -- UTC. 봉의 시작 시각
    open            NUMERIC(20,4) NOT NULL,
    high            NUMERIC(20,4) NOT NULL,
    low             NUMERIC(20,4) NOT NULL,
    close           NUMERIC(20,4) NOT NULL,
    volume          BIGINT NOT NULL,
    PRIMARY KEY (stock_id, ts)
) PARTITION BY RANGE (ts);

-- 파티션 경계는 UTC 기준이다. 세션 타임존에 의존하지 않도록 오프셋을 명시한다.
-- 이후 월분은 운영 배치가 미리 생성해야 한다. (ROADMAP Phase 2)
DO $$
DECLARE
    m    TIMESTAMPTZ := TIMESTAMPTZ '2026-08-01 00:00:00+00';
    stop TIMESTAMPTZ := TIMESTAMPTZ '2028-01-01 00:00:00+00';
BEGIN
    WHILE m < stop LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS price_minute_%s PARTITION OF price_minute FOR VALUES FROM (%L) TO (%L)',
            to_char(m AT TIME ZONE 'UTC', 'YYYYMM'),
            m,
            m + INTERVAL '1 month'
        );
        m := m + INTERVAL '1 month';
    END LOOP;
END $$;

CREATE TABLE trading_flow (
    stock_id        TEXT NOT NULL REFERENCES stock(stock_id),
    trade_date      DATE NOT NULL,
    foreign_net     NUMERIC(20,0),
    institution_net NUMERIC(20,0),
    individual_net  NUMERIC(20,0),
    PRIMARY KEY (stock_id, trade_date)
);

-- =====================================================================
-- 3. 정보수집
-- =====================================================================

CREATE TABLE source (
    source_id       SERIAL PRIMARY KEY,
    kind            TEXT NOT NULL,           -- 'telegram' | 'dart'
    identifier      TEXT NOT NULL,
    name            TEXT NOT NULL,
    weight          NUMERIC(4,2) DEFAULT 1.0,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    last_success_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (kind, identifier)
);

CREATE TABLE raw_message (
    message_id      BIGSERIAL PRIMARY KEY,
    source_id       INT NOT NULL REFERENCES source(source_id),
    external_id     TEXT,
    content         TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    published_at    TIMESTAMPTZ NOT NULL,
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_filtered     BOOLEAN DEFAULT FALSE,
    analyzed_at     TIMESTAMPTZ,             -- NULL이면 미분석
    analysis_method TEXT,                    -- 'dict' | 'llm'
    UNIQUE (source_id, content_hash)
);

CREATE INDEX idx_raw_published ON raw_message(published_at DESC);
CREATE INDEX idx_raw_unanalyzed ON raw_message(analyzed_at) WHERE analyzed_at IS NULL;

CREATE TABLE keyword (
    keyword_id      SERIAL PRIMARY KEY,
    term            TEXT NOT NULL UNIQUE,
    canonical_id    INT REFERENCES keyword(keyword_id),  -- NULL이면 자기 자신이 대표어
    category        TEXT,                    -- 'industry' | 'tech' | 'theme'
    is_confirmed    BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE keyword_mention (
    message_id      BIGINT NOT NULL REFERENCES raw_message(message_id),
    keyword_id      INT NOT NULL REFERENCES keyword(keyword_id),
    PRIMARY KEY (message_id, keyword_id)
);

CREATE TABLE keyword_daily (
    keyword_id      INT NOT NULL REFERENCES keyword(keyword_id),
    trade_date      DATE NOT NULL,
    mention_count   INT NOT NULL,
    weighted_count  NUMERIC(10,2),
    ma7             NUMERIC(10,2),
    surge_ratio     NUMERIC(10,2),           -- mention_count / ma7. 이 값이 신호다
    PRIMARY KEY (keyword_id, trade_date)
);

CREATE INDEX idx_keyword_surge ON keyword_daily(trade_date, surge_ratio DESC);

CREATE TABLE stock_mention (
    message_id      BIGINT NOT NULL REFERENCES raw_message(message_id),
    stock_id        TEXT NOT NULL REFERENCES stock(stock_id),
    PRIMARY KEY (message_id, stock_id)
);

CREATE TABLE dart_disclosure (
    rcept_no        TEXT PRIMARY KEY,        -- 접수번호
    stock_id        TEXT REFERENCES stock(stock_id),
    corp_name       TEXT NOT NULL,
    report_name     TEXT NOT NULL,
    disclosure_type TEXT,
    submitted_at    TIMESTAMPTZ NOT NULL,
    url             TEXT,
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =====================================================================
-- 4. 시장분석
-- =====================================================================

CREATE TABLE indicator (
    indicator_code  TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    layer           TEXT NOT NULL,           -- 'sentiment'|'risk'|'position'|'fundamental'
    frequency       TEXT NOT NULL,           -- 'daily' | 'monthly'
    source          TEXT NOT NULL,
    unit            TEXT,
    use_in_regime   BOOLEAN NOT NULL DEFAULT TRUE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE indicator_value (
    indicator_code  TEXT NOT NULL REFERENCES indicator(indicator_code),
    period_date     DATE NOT NULL,           -- 일간은 해당일, 월간은 월초
    value           NUMERIC(20,6) NOT NULL,
    change_rate     NUMERIC(10,4),
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (indicator_code, period_date)
);

-- 판정 이력. indicators 스냅샷이 나중에 규칙을 검증하는 근거가 된다.
CREATE TABLE market_regime (
    trade_date      DATE PRIMARY KEY,
    regime          TEXT NOT NULL,           -- 'danger' | 'neutral' | 'safe'
    score           NUMERIC(6,3) NOT NULL,
    layer_scores    JSONB,
    indicators      JSONB NOT NULL,          -- 판정 시점의 지표값 스냅샷
    rule_version    TEXT NOT NULL,
    is_override     BOOLEAN NOT NULL DEFAULT FALSE,
    override_reason TEXT,
    kospi_return    NUMERIC(10,4),           -- 익일 채움
    kosdaq_return   NUMERIC(10,4),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =====================================================================
-- 5. 매매
-- =====================================================================

-- 엔진은 stock_id, side, strength 만 보고 동작한다. payload 내용은 알지 못한다.
CREATE TABLE signal (
    signal_id       BIGSERIAL PRIMARY KEY,
    stock_id        TEXT NOT NULL REFERENCES stock(stock_id),
    strategy        TEXT NOT NULL,           -- 'daytrade' | 'swing'
    side            TEXT NOT NULL,           -- 'BUY' | 'SELL'
    strength        NUMERIC(5,2),            -- 0~100
    payload         JSONB,
    regime_at       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    consumed_at     TIMESTAMPTZ
);

CREATE INDEX idx_signal_pending ON signal(strategy, created_at)
    WHERE consumed_at IS NULL;

-- client_order_id 는 우리 쪽 멱등성 키다. INSERT 전에 애플리케이션이 ULID 로 생성한다.
-- UNIQUE 제약이 중복 주문을 DB 수준에서 차단한다. (A-2)
CREATE TABLE order_request (
    order_id        BIGSERIAL PRIMARY KEY,
    client_order_id TEXT NOT NULL UNIQUE,
    account_id      TEXT NOT NULL REFERENCES account(account_id),
    stock_id        TEXT NOT NULL REFERENCES stock(stock_id),
    signal_id       BIGINT REFERENCES signal(signal_id),
    side            TEXT NOT NULL,           -- 'BUY' | 'SELL'
    order_type      TEXT NOT NULL,           -- 'LIMIT' | 'MARKET'
    quantity        INT NOT NULL,
    price           NUMERIC(20,4),           -- 시장가는 NULL
    currency        TEXT NOT NULL DEFAULT 'KRW',
    status          TEXT NOT NULL,           -- pending|submitted|partial|filled|cancelled|rejected
    broker_order_no TEXT,
    filled_qty      INT NOT NULL DEFAULT 0,
    avg_fill_price  NUMERIC(20,4),
    error_message   TEXT,
    is_manual       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_order_account_date ON order_request(account_id, created_at DESC);
CREATE INDEX idx_order_open ON order_request(status)
    WHERE status IN ('pending','submitted','partial');

-- 수수료와 세금을 반드시 기록한다. 백테스트 검증 시 실제 비용과 대조한다.
CREATE TABLE execution (
    execution_id    BIGSERIAL PRIMARY KEY,
    order_id        BIGINT NOT NULL REFERENCES order_request(order_id),
    quantity        INT NOT NULL,
    price           NUMERIC(20,4) NOT NULL,
    fee             NUMERIC(20,4) DEFAULT 0,
    tax             NUMERIC(20,4) DEFAULT 0,
    executed_at     TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_execution_order ON execution(order_id);

-- 증권사 잔고가 정본이다. 이 테이블은 캐시다.
-- opened_at 은 NULL 을 허용한다. 동기화로 발견된 포지션은 최초 취득 시각을 알 수 없다. (A-4)
CREATE TABLE position (
    account_id      TEXT NOT NULL REFERENCES account(account_id),
    stock_id        TEXT NOT NULL REFERENCES stock(stock_id),
    quantity        INT NOT NULL,
    avg_price       NUMERIC(20,4) NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'KRW',
    opened_at       TIMESTAMPTZ,
    synced_at       TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (account_id, stock_id)
);

CREATE TABLE daily_pnl (
    account_id      TEXT NOT NULL REFERENCES account(account_id),
    trade_date      DATE NOT NULL,
    deposit         NUMERIC(20,4),
    eval_amount     NUMERIC(20,4),
    total_asset     NUMERIC(20,4),
    realized_pnl    NUMERIC(20,4),
    unrealized_pnl  NUMERIC(20,4),
    trade_count     INT DEFAULT 0,
    currency        TEXT NOT NULL DEFAULT 'KRW',
    PRIMARY KEY (account_id, trade_date)
);

CREATE TABLE stock_filter (
    filter_id       SERIAL PRIMARY KEY,
    stock_id        TEXT NOT NULL REFERENCES stock(stock_id),
    strategy        TEXT NOT NULL,           -- 'daytrade'|'swing'|'all'
    filter_type     TEXT NOT NULL,           -- 'block' | 'allow'
    reason          TEXT,
    until_date      DATE,                    -- NULL이면 무기한
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_stock_filter_lookup ON stock_filter(strategy, stock_id);

-- =====================================================================
-- 6. 운영
-- =====================================================================

-- 엔진과 포털은 HTTP 가 아니라 이 테이블로 통신한다.
CREATE TABLE heartbeat (
    process_name    TEXT PRIMARY KEY,
    status          TEXT NOT NULL,           -- 'running'|'idle'|'stopping'|'error'
    last_beat_at    TIMESTAMPTZ NOT NULL,
    detail          JSONB,
    started_at      TIMESTAMPTZ
);

-- 포털 -> 엔진 제어. 엔진이 폴링한다.
-- halt_entry(신규 진입 차단)와 liquidate_all(전량 청산)은 별개 명령이다.
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

-- 당시 어떤 파라미터로 돌았는지를 남긴다. 파라미터를 설정 파일로만 바꾸는 원칙과 짝을 이룬다.
CREATE TABLE engine_run (
    run_id          BIGSERIAL PRIMARY KEY,
    process_name    TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL,
    stopped_at      TIMESTAMPTZ,
    config_snapshot JSONB NOT NULL,
    exit_reason     TEXT
);

CREATE TABLE event_log (
    event_id        BIGSERIAL PRIMARY KEY,
    process_name    TEXT NOT NULL,
    level           TEXT NOT NULL,           -- 'INFO'|'WARN'|'ERROR'|'CRITICAL'
    category        TEXT,                    -- 'order'|'collect'|'regime'|'system'
    message         TEXT NOT NULL,
    detail          JSONB,
    notified        BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_event_recent ON event_log(created_at DESC);
CREATE INDEX idx_event_error ON event_log(level, created_at DESC)
    WHERE level IN ('ERROR','CRITICAL');

-- 일일 상한 판단에 사용한다. 초과 시 LLM 호출을 중단한다.
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

-- =====================================================================
-- 7. 백테스트
-- =====================================================================

-- fee_rate 와 slippage_rate 는 NOT NULL 이다. 0으로 돌린 결과도 그 사실이 남는다.
CREATE TABLE backtest_run (
    run_id          BIGSERIAL PRIMARY KEY,
    strategy        TEXT NOT NULL,
    from_date       DATE NOT NULL,
    to_date         DATE NOT NULL,
    universe        TEXT,
    params          JSONB NOT NULL,
    initial_capital NUMERIC(20,4) NOT NULL,
    final_capital   NUMERIC(20,4),
    currency        TEXT NOT NULL DEFAULT 'KRW',
    total_return    NUMERIC(10,4),
    mdd             NUMERIC(10,4),
    win_rate        NUMERIC(10,4),
    trade_count     INT,
    sharpe          NUMERIC(10,4),
    fee_rate        NUMERIC(10,6) NOT NULL,
    slippage_rate   NUMERIC(10,6) NOT NULL,
    note            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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

CREATE INDEX idx_backtest_trade_run ON backtest_trade(run_id);
