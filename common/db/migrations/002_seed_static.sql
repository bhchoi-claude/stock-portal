-- 정적 기준 데이터. 외부 조회 없이 값이 고정된 것만 넣는다.
--
-- stock, exchange_holiday, account, source 는 외부 데이터나 실제 값이 필요하므로
-- 별도 적재 스크립트로 남긴다. (ROADMAP Phase 1 "기준 데이터 적재")
--
-- 재적용이 안전하도록 전부 ON CONFLICT DO NOTHING 을 쓴다.

-- =====================================================================
-- exchange
-- =====================================================================
-- KOSPI 와 KOSDAQ 은 거래시간이 같은 하나의 거래소이므로 행을 나누지 않는다. (A-1)
-- 시장 구분은 stock.board 에서 관리한다.

INSERT INTO exchange (exchange, name, country, currency, timezone, open_time, close_time, is_active)
VALUES ('KRX', '한국거래소', 'KR', 'KRW', 'Asia/Seoul', '09:00', '15:30', TRUE)
ON CONFLICT (exchange) DO NOTHING;

-- =====================================================================
-- indicator (8종)
-- =====================================================================
-- 금리·M2·가계부채는 갱신이 느려 일 단위 판정에 부적합하므로 제외한다.
-- 나중에 참고용으로 추가할 때는 use_in_regime = FALSE 로 넣는다.

INSERT INTO indicator (indicator_code, name, layer, frequency, source, unit, use_in_regime, is_active)
VALUES
    ('DEPOSIT',         '투자자예탁금',            'sentiment',   'daily',   'kofia',   '억원', TRUE, TRUE),
    ('CREDIT_BALANCE',  '신용잔고',                'sentiment',   'daily',   'kofia',   '억원', TRUE, TRUE),
    ('FOREIGN_NET',     '외국인 순매수',           'sentiment',   'daily',   'krx',     '억원', TRUE, TRUE),
    ('VKOSPI',          'VKOSPI 변동성지수',       'risk',        'daily',   'krx',     'pt',   TRUE, TRUE),
    ('USDKRW',          '원달러 환율',             'risk',        'daily',   'ecos',    'KRW',  TRUE, TRUE),
    ('KOSPI_MA200_GAP', 'KOSPI 200일 이평 이격도', 'position',    'daily',   'derived', '%',    TRUE, TRUE),
    ('EXPORT_YOY',      '수출 증가율',             'fundamental', 'monthly', 'customs', '%',    TRUE, TRUE),
    ('EXPORT_SEMI_YOY', '반도체 수출 증가율',      'fundamental', 'monthly', 'customs', '%',    TRUE, TRUE)
ON CONFLICT (indicator_code) DO NOTHING;
