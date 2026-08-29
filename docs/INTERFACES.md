# INTERFACES.md — 모듈 인터페이스 규격

> 모듈 간 경계를 정의한다. 각 구현체는 여기 정의된 시그니처를 지킨다.
> `PROJECT.md` 8장의 설계 원칙, `SCHEMA.md`의 테이블 정의를 전제로 한다.
>
> 언어는 Python. 타입 힌트는 필수로 작성한다.

---

## 0. 의존 방향

```
        [Strategy]  ← 전략. 아래 계층을 알지 못한다
             │
        [DataFeed]  ← 데이터 공급 추상화
             │
    ┌────────┴────────┐
 LiveFeed        BacktestFeed
    │
[Broker]  ← 증권사 추상화
    │
KiwoomBroker
```

**위쪽이 아래쪽을 모른다.**

- 전략은 어느 증권사인지, 실전인지 백테스트인지 알지 못한다
- `DataFeed`는 전략의 내용을 알지 못한다
- `Broker`는 전략도 피드도 알지 못한다

이 방향이 깨지면 백테스트와 실전이 갈라진다.

---

## 1. 공통 타입

```python
from dataclasses import dataclass
from datetime import datetime, date
from decimal import Decimal
from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class Regime(str, Enum):
    DANGER = "danger"
    NEUTRAL = "neutral"
    SAFE = "safe"


@dataclass(frozen=True)
class Candle:
    stock_id: str
    ts: datetime          # UTC. 봉의 시작 시각
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class Quote:
    stock_id: str
    ts: datetime          # UTC
    price: Decimal
    bid: Decimal | None
    ask: Decimal | None
    volume: int


@dataclass(frozen=True)
class Position:
    account_id: str
    stock_id: str
    quantity: int
    avg_price: Decimal
    currency: str = "KRW"


@dataclass(frozen=True)
class Balance:
    account_id: str
    deposit: Decimal          # 예수금
    available: Decimal        # 주문가능금액
    eval_amount: Decimal      # 평가금액
    total_asset: Decimal
    currency: str = "KRW"
```

**금액은 전부 `Decimal`을 쓴다.** `float`는 사용하지 않는다.
누적 오차가 손익 계산을 틀리게 만든다.

---

## 2. Broker — 증권사 어댑터

증권사 종속을 격리한다. 현재 구현체는 `KiwoomBroker` 하나.

```python
from abc import ABC, abstractmethod


class Broker(ABC):
    """증권사 API 추상화."""

    name: str                 # 'kiwoom'

    # ---- 조회 ----

    @abstractmethod
    def get_quote(self, stock_id: str) -> Quote: ...

    @abstractmethod
    def get_candles(
        self, stock_id: str, interval: str, count: int,
        end: datetime | None = None,
    ) -> list[Candle]:
        """interval: '1m' | '5m' | '1d'. 최신순이 아니라 시간 오름차순 반환."""

    @abstractmethod
    def get_balance(self, account_id: str) -> Balance: ...

    @abstractmethod
    def get_positions(self, account_id: str) -> list[Position]: ...

    # ---- 주문 ----

    @abstractmethod
    def submit_order(self, req: "OrderRequest") -> "OrderResult": ...

    @abstractmethod
    def cancel_order(self, account_id: str, broker_order_no: str) -> "OrderResult": ...

    @abstractmethod
    def get_order_status(self, account_id: str, broker_order_no: str) -> "OrderResult": ...

    # ---- 실시간 ----

    @abstractmethod
    def subscribe(self, stock_ids: list[str], on_quote) -> None:
        """웹소켓 구독. on_quote(Quote)로 콜백."""

    @abstractmethod
    def unsubscribe(self, stock_ids: list[str]) -> None: ...
```

### 주문 타입

```python
@dataclass
class OrderRequest:
    client_order_id: str      # 중복 방지 키. 필수
    account_id: str
    stock_id: str
    side: Side
    order_type: OrderType
    quantity: int
    price: Decimal | None = None      # MARKET이면 None


@dataclass
class OrderResult:
    client_order_id: str
    broker_order_no: str | None
    status: str               # 'submitted'|'partial'|'filled'|'cancelled'|'rejected'
    filled_qty: int
    avg_fill_price: Decimal | None
    error_code: str | None
    error_message: str | None
```

### 2.1 중복 주문 방지 — 반드시 지킨다

`client_order_id`는 **주문을 내기 전에** DB에 기록한다.
네트워크 오류로 응답을 못 받았을 때, 재시도가 중복 주문이 되면 안 된다.

```
1. client_order_id 생성 (ULID)
2. order_request INSERT (status='pending')
3. broker.submit_order() 호출
4. 응답 수신 → status 갱신
```

3번에서 예외가 나면 **자동 재시도하지 않는다.**
`get_order_status`로 실제 접수 여부를 조회한 뒤 판단한다.

`client_order_id`는 **ULID**를 쓴다. 애플리케이션이 INSERT 전에 생성하며
`order_id`를 참조하지 않는다. 채번 순환이 생기지 않는다.

이 값은 **우리 쪽 멱등성 키**다. 증권사가 사용자 지정 주문 ID를 지원하는지와
무관하게 사용한다. `order_request.client_order_id`의 UNIQUE 제약이
중복 주문을 DB 수준에서 차단한다.

### 2.2 재시작 복구

엔진 시작 시 `status IN ('pending','submitted','partial')`인 주문을 전부 조회해서
증권사 상태와 대조한 뒤에 매매를 시작한다. 이 과정이 끝나기 전에는 신규 주문을 내지 않는다.

### 2.3 에러 분류

```python
class BrokerError(Exception): ...
class TransientError(BrokerError):   # 재시도 가능 (타임아웃, 일시 장애)
    ...
class PermanentError(BrokerError):   # 재시도 불가 (잔고부족, 잘못된 종목)
    ...
class RateLimitError(TransientError):
    retry_after: float
```

조회는 `TransientError`에 한해 지수 백오프로 재시도한다.
**주문은 자동 재시도하지 않는다.** 2.1 참조.

### 2.4 키움 REST 실측 규격

2026-08-25 모의투자 계좌로 확인한 값이다. 추측이 아니라 실제 응답이다.

**도메인**

| 구분 | 도메인 |
|---|---|
| 모의투자 | `https://mockapi.kiwoom.com` |
| 실전 | `https://api.kiwoom.com` |

**실전과 모의는 앱키가 별도다.** 모의 앱키로 실전 도메인에 접속하면
`투자구분(실전/모의)이 달라서 Appkey를 사용할수가 없습니다` 로 거부된다.
어댑터는 `account.is_paper` 로 도메인과 키를 함께 골라야 한다.

**접근토큰**

```
POST /oauth2/token
Content-Type: application/json;charset=UTF-8
{ "grant_type": "client_credentials", "appkey": "...", "secretkey": "..." }
```

응답은 `token`, `token_type`(`Bearer`), `expires_dt`, `return_code`, `return_msg`.
`return_code = 0` 이 정상이다. **HTTP 200 이어도 `return_code` 를 봐야 한다.**

`expires_dt` 는 `YYYYMMDDHHMMSS` 이고 발급 시점 기준 **약 24시간** 뒤다.
관측상 **KST 로 보인다.** UTC 로 파싱하면 9시간 어긋나므로 `Asia/Seoul` 로 읽는다.
토큰은 캐시하고 만료 전에 갱신한다. 매 호출마다 발급받지 않는다.

**조회 요청**

```
POST /api/dostk/stkinfo
authorization: Bearer {token}
api-id: ka10001
{ "stk_cd": "005930" }
```

**`stk_cd` 는 거래소 접두어 없는 순수 종목코드다.**
`KRX:005930` 을 보내면 `return_code = 5` 로 거부된다.

따라서 어댑터는 `stock_id` 를 그대로 넘기지 않고 `stock.code` 를 쓴다.
`stock_id` 에서 접두어를 떼는 변환이 브로커 경계에 들어간다.
이 변환은 **어댑터 안에만** 둔다. 전략과 피드는 `stock_id` 만 다룬다.

`ka10001`(주식기본정보요청) 응답은 47개 필드이며 `stk_nm`, `cur_prc`,
`open_pric`, `high_pric`, `low_pric`, `base_pric`, `per`, `eps`, `pbr` 등을 담는다.

**분봉 — `ka10080` (2026-08-28 실측)**

```
POST /api/dostk/chart
api-id: ka10080
{ "stk_cd": "005930", "tic_scope": "1", "upd_stkpc_tp": "0" }
```

응답 목록은 `stk_min_pole_chart_qry` 이고 **한 번에 900건**이 온다.
1분봉 900건은 약 2.3 거래일이다. 응답 헤더의 `cont-yn` 이 `Y` 면
`next-key` 로 이어 받는다.

| 필드 | 내용 |
|---|---|
| `cntr_tm` | `YYYYMMDDHHMMSS`. **KST** 이며 봉의 시작 시각 |
| `open_pric` / `high_pric` / `low_pric` / `cur_prc` | 시·고·저·종가 |
| `trde_qty` | 봉 거래량 |
| `acc_trde_qty` | 누적 거래량. 쓰지 않는다 |

**가격에 부호가 붙는다.** `"open_pric": "-257000"` 은 음수 가격이 아니라
전일대비 방향 표시다. 900건 3600개 값 중 `-` 1520개, `+` 2076개,
부호 없음 4개였다. **`abs()` 로 벗기지 않으면 음수 가격이 저장된다.**

일봉(`ka10081`)은 `cur_prc` 에 부호가 없고 `pred_pre` 에만 붙는다.
**API 마다 다르므로 필드별로 확인한다.**

목록은 **최신순**이다. `INTERFACES.md` 2장이 요구하는 시간 오름차순으로
뒤집어 돌려준다.

**호출 한도 — `유량=1` (2026-08-28 실측)**

같은 `api-id` 를 연속 호출하면 즉시 HTTP 429 다.
응답 본문에 `허용된 API 요청 개수를 초과하였습니다. 유량=1` 이 온다.

| 간격 | 결과 |
|---|---|
| 0.0초 | 2회째 429 |
| 0.5초 | 8회 중 2회 429 |
| **1.0초** | **8회 모두 성공** |
| 1.5초 / 2.0초 | 8회 모두 성공 |

한도는 `api-id` 별로 걸린다. 다른 `api-id` 를 섞어 부르면 걸리지 않았다.
**1초 간격을 지킨다.** 429 는 `RateLimitError` 로 분류한다.

---

## 2.5 지표 소스 실측 규격 (2026-08-29)

인증키를 받아 실제로 호출해 확인한 값이다.

### data.go.kr — 서비스키는 Encoding 형태로 온다

포털이 주는 '일반 인증키' 는 `%` 가 섞인 **Encoding 형태**다.
그대로 `urlencode` 하면 `%` 가 `%25` 로 이중 인코딩돼
`SERVICE_KEY_IS_NOT_REGISTERED_ERROR` (HTTP 403) 가 난다.

**`unquote` 로 한 번 푼 뒤 인코딩한다.** 그러면 Encoding·Decoding 어느 값을
넣어도 동작한다. 사용자가 어느 쪽을 넣을지 코드가 정할 수 없다.

| 지표 | Base URL | 오퍼레이션 |
|---|---|---|
| `DEPOSIT` | `apis.data.go.kr/1160100/service/GetKofiaStatisticsInfoService` | `getSecuritiesMarketTotalCapitalInfo` |
| `CREDIT_BALANCE` | 위와 같음 | `getGrantingOfCreditBalanceInfo` |
| `EXPORT_YOY` | `apis.data.go.kr/1220000/Newtrade` | `getNewtradeList` |
| `EXPORT_SEMI_YOY` | `apis.data.go.kr/1220000/Itemtrade` | `getItemtradeList` |

**금융투자협회 두 건은 `resultType=json` 이 먹지만 관세청 두 건은 XML 만 온다.**

**증시자금추이** — 일별, 총 1185건

| 필드 | 내용 |
|---|---|
| `basDt` | 기준일자 `YYYYMMDD` |
| `invrDpsgAmt` | **투자자예탁금** (원). 2026-08-26 에 98.9조 |
| `brkTrdUcolMny` | 위탁매매미수금 |

**신용공여잔고추이** — 일별, 총 1172건

| 필드 | 내용 |
|---|---|
| `basDt` | 기준일자 |
| `crdTrFingWhl` | **신용거래융자 전체** (원). 2026-08-26 에 33.1조 |
| `crdTrFingScrs` / `crdTrFingKosdaq` | 시장별 내역 |
| `dpsgScrtMogFing` | 예탁증권담보융자 |

**수출입총괄** — 월별. `strtYymm` / `endYymm` 는 `YYYYMM`

| 필드 | 내용 |
|---|---|
| `year` | `2026.06` 형식. **마지막 행은 `총계` 다. 걸러야 한다** |
| `expDlr` | 수출액 (미국달러) |
| `impDlr` | 수입액 |

**품목별 수출입실적** — 월별, `hsSgn` 으로 HS 코드 지정.
`hsSgn=8542` 는 집적회로이고 응답은 10자리 세부코드로 쪼개져 온다
(`8542311000` 모노리식, `8542321010` 디램 등). **합산이 필요하다.**

`year` 에 `총계` 행이 섞이는 것은 총괄과 같다.

### DART — 자기 고유번호로만 조회된다 (2026-08-29 실측)

종목코드로는 조회가 안 된다. `corpCode.xml` (3.6MB zip) 이 매핑을 준다.
118,804건 중 상장사는 3,988건이다. **우선주는 목록에 없다.** 회사 단위로
등록되므로 보통주 코드로 찾아야 한다 (`001465` -> `001460`).

| 용도 | 오퍼레이션 |
|---|---|
| 고유번호 매핑 | `corpCode.xml` |
| 증자·감자 이력 | `irdsSttus.json` (`bsns_year`, `reprt_code=11011`) |
| 배당 | `alotMatter.json` — 연 단위. 배당락일이 아니라 결산기준일이다 |

`irdsSttus` 의 `isu_dcrs_stle` 이 발행형태를 준다.

| 발행형태 | 가격 조정 |
|---|---|
| 무상증자, 주식분할 | TRUE — 대가 없이 늘어 가격이 기계적으로 나뉜다 |
| 유상증자, 전환권행사, 신주인수권행사, 스톡옵션 | FALSE — 대가를 받는다 |

**날짜로 맞추면 안 된다.** `isu_dcrs_de` 는 발행일이고 상장주식수 변경일과
며칠씩 어긋난다 (삼양홀딩스 18일). **수량으로 맞춘다.**
다만 분할에서는 증가분이 아니라 **발행 후 총수**를 적기도 하므로 둘 다 받는다.

`status = 013` 은 데이터 없음이고 오류가 아니다.

### ECOS — 인증키 하나로 모든 통계표를 조회한다

`StatisticSearch/{KEY}/json/kr/{시작}/{끝}/{통계표}/{주기}/{시작일}/{종료일}/{항목}`

| 지표 | 통계표 | 주기 | 항목 |
|---|---|---|---|
| `USDKRW` | `731Y001` 주요국 통화의 대원화환율 | `D` | `0000001` 원/미국달러(매매기준율) |

응답은 `StatisticSearch.row[]` 이고 `TIME` (`YYYYMMDD`), `DATA_VALUE` (문자열) 를 쓴다.
1964-05-04 부터 있다.

---

## 3. DataFeed — 데이터 공급

**백테스트와 실전이 갈라지지 않게 하는 핵심 지점.**

```python
class DataFeed(ABC):

    @abstractmethod
    def now(self) -> datetime:
        """현재 시각(UTC). 백테스트에서는 시뮬레이션 시각."""

    @abstractmethod
    def get_candles(self, stock_id: str, interval: str, count: int) -> list[Candle]:
        """now() 시점까지의 봉. 미래 데이터는 절대 포함하지 않는다."""

    @abstractmethod
    def get_quote(self, stock_id: str) -> Quote: ...

    @abstractmethod
    def get_universe(self) -> list[str]:
        """유니버스 필터 + stock_filter 적용 후의 매매 대상 종목."""

    @abstractmethod
    def get_regime(self) -> Regime: ...

    @abstractmethod
    def get_signals(self, strategy: str, since: datetime) -> list["SignalRecord"]:
        """정보수집이 만든 시그널. 참고 지표로 사용."""
```

### 3.1 `datetime.now()` 직접 호출 금지

전략 코드와 피드 구현체 안에서 `datetime.now()`를 호출하면 안 된다.
반드시 `feed.now()`를 쓴다.

이 규칙을 어기면 백테스트가 미래를 참조하게 되고, 결과가 실전과 달라진다.
가장 흔하고 가장 발견하기 어려운 버그다.

### 3.2 구현체

| 구현체 | 데이터 출처 | `now()` |
|---|---|---|
| `LiveFeed` | Broker + DB | 실제 시각 |
| `BacktestFeed` | DB (price_daily, price_minute) | 시뮬레이션 커서 |

`BacktestFeed.get_candles()`는 **커서 시각 이후의 데이터를 반환하면 안 된다.**
구현 시 이 조건을 단위 테스트로 강제한다.

### 3.3 수정주가

`get_candles()`는 **조정가를 반환한다.** `price_daily`에 저장된 원주가에
`adj_factor`를 곱한 값이고, 거래량은 `adj_factor`로 나눈 값이다.

지표 계산이 분할 시점에서 끊기지 않게 하기 위한 것이다.
조정하지 않으면 분할일이 급락으로 보여 손절이 대량 발동한다.

주문 가격 산출에는 영향이 없다. 최신 봉의 `adj_factor`는 항상 `1.0`이고,
주문은 현재가와 호가를 기준으로 내기 때문이다.

`get_quote()`는 조정하지 않는다. 현재가는 조정 대상이 아니다.

분봉에는 조정계수가 없다. (`SCHEMA.md` 2장)

### 3.4 과거 유니버스

`get_universe()`는 `now()` 시점의 유니버스를 반환한다.
종목 상태는 `stock`의 현재값이 아니라 `stock_status`에서 **해당 시점 값**을 읽는다.

상장폐지 종목도 폐지 이전 시점에서는 유니버스에 포함된다.
제외하면 생존편향이 생긴다. (`PROJECT.md` 11장)

---

## 4. Strategy — 전략 플러그인

전략의 내용은 본 문서 범위 밖이다. 여기서는 **껍데기 규격만** 정의한다.

```python
@dataclass
class Context:
    feed: DataFeed
    account_id: str
    params: dict              # config에서 로드된 파라미터
    positions: dict[str, Position]
    balance: Balance


@dataclass
class EntryIntent:
    stock_id: str
    side: Side
    strength: Decimal         # 0~100
    payload: dict             # 진입 근거. 자유 형식
    limit_price: Decimal | None = None


@dataclass
class ExitIntent:
    stock_id: str
    quantity: int             # 부분 청산 가능
    reason: str               # 'target'|'stop'|'timeout'|'signal'
    limit_price: Decimal | None = None


class Strategy(ABC):
    name: str                 # 'daytrade' | 'swing'

    @abstractmethod
    def scan(self, ctx: Context) -> list[EntryIntent]:
        """진입 후보 탐색."""

    @abstractmethod
    def manage(self, ctx: Context, position: Position) -> ExitIntent | None:
        """보유 포지션의 청산 판단. 포지션마다 호출된다."""

    def on_start(self, ctx: Context) -> None: ...
    def on_day_end(self, ctx: Context) -> None: ...
```

### 4.1 진입과 청산을 분리하는 이유

`manage()`는 `scan()`과 독립적으로 매 주기 호출된다.
신규 진입을 차단(`halt_entry`)한 상태에서도 청산은 계속 동작해야 하기 때문이다.

### 4.2 전략은 주문을 내지 않는다

`Strategy`는 **의도(Intent)만 반환**한다. 실제 주문은 엔진이 낸다.
포지션 크기 계산, 리스크 한도 확인, 주문 실행은 전략의 책임이 아니다.

이 분리 덕분에 같은 전략 코드가 백테스트에서도 그대로 돌아간다.

### 4.3 파라미터

`ctx.params`로만 접근한다. 전략 코드에 숫자를 직접 쓰지 않는다.

```python
# 금지
if profit_rate > 0.03: ...

# 올바름
if profit_rate > ctx.params["take_profit"]: ...
```

---

## 5. RiskManager — 포지션 크기와 한도

엔진이 `EntryIntent`를 실제 주문으로 바꿀 때 통과시키는 계층.

```python
@dataclass
class RiskDecision:
    approved: bool
    quantity: int
    reason: str | None        # 거부 사유


class RiskManager:

    def evaluate(
        self, intent: EntryIntent, ctx: Context, regime: Regime,
    ) -> RiskDecision:
        """포지션 크기 산정 및 한도 확인."""
```

확인 항목:

| 항목 | 설정 키 |
|---|---|
| 국면별 자금 배분 비율 | `regime_allocation` |
| 건당 최대 투입 금액 | `max_position_size` |
| 종목당 최대 비중 | `max_weight_per_stock` |
| 동시 보유 종목 수 | `max_positions` |
| 일일 손실 한도 | `daily_loss_limit` |
| 주문가능금액 | (Broker 조회) |

**일일 손실 한도에 도달하면 당일 신규 진입을 중단한다.**
이는 전략과 무관한 계층에서 강제한다.

---

## 6. Collector — 수집기 플러그인

소스 하나가 실패해도 나머지는 동작한다.

```python
class Collector(ABC):
    source_kind: str          # 'telegram' | 'dart' | 'krx' | 'kofia' | 'customs'
    interval_sec: int

    @abstractmethod
    def collect(self, since: datetime) -> "CollectResult": ...
```

```python
@dataclass
class CollectResult:
    success: bool
    records: list             # MessageRecord 또는 IndicatorRecord
    error: str | None = None
    next_since: datetime | None = None
```

### 6.1 두 종류

```python
@dataclass
class MessageRecord:
    """raw_message로 적재."""
    external_id: str | None
    content: str
    published_at: datetime    # UTC


@dataclass
class IndicatorRecord:
    """indicator_value로 적재."""
    indicator_code: str
    period_date: date
    value: Decimal
```

수집기가 어디서 어떻게 가져오든 이 형식으로 반환한다.
**판정 엔진과 분석기는 데이터 출처를 알지 못한다.**

### 6.2 실패 처리

- 한 수집기의 예외가 다른 수집기에 전파되면 안 된다
- 실패는 `event_log`에 기록하고 다음 주기에 재시도한다
- 1시간 내 반복 실패 시 알림 (`PROJECT.md` 10장)
- `source.last_success_at`을 갱신한다

---

## 7. NewsAnalyzer — 정보수집 분석

`PROJECT.md` 9장의 처리 순서를 구현한다.

```python
class NewsAnalyzer:

    def filter(self, records: list[MessageRecord]) -> list[MessageRecord]:
        """규칙 기반 필터. 광고, 중복(해시), 최소 길이 미달 제거."""

    def match_stocks(self, content: str) -> list[str]:
        """상장사 명단 사전 매칭. LLM 미사용."""

    def match_keywords(self, content: str) -> tuple[list[int], str]:
        """키워드 사전 매칭. 반환: (매칭된 keyword_id, 미매칭 잔여 텍스트)"""

    def extract_keywords_llm(self, contents: list[str]) -> list[list[str]]:
        """미매칭분만 배치 호출. 입력 순서와 출력 순서가 일치해야 한다."""
```

### 7.1 LLM 호출 규약

- 모델: 가벼운 모델(Haiku 계열)
- **단건 호출 금지.** 최소 N건 모으거나 최대 대기 시간 경과 시 배치 발송
- 출력은 JSON 배열만. 전문(preamble)이나 코드펜스를 허용하지 않는 프롬프트 사용
- 파싱 실패 시 해당 배치는 `analyzed_at`을 채우지 않고 다음 주기에 재시도
- 호출 전 `api_usage`의 당일 누적을 확인. **일일 상한 초과 시 호출하지 않고 알림**

### 7.2 새 키워드 처리

LLM이 사전에 없는 표현을 반환하면 `keyword`에 `is_confirmed=FALSE`로 삽입한다.
동의어 병합은 사용자가 화면에서 수행한다. 자동 병합하지 않는다.

---

## 8. RegimeEngine — 국면 판정

규칙은 코드가 아니라 설정 파일에 둔다.

```python
@dataclass
class RegimeResult:
    regime: Regime
    score: Decimal
    layer_scores: dict[str, Decimal]
    indicators: dict[str, Decimal]    # 판정에 쓴 값 스냅샷
    rule_version: str


class RegimeEngine:

    def __init__(self, rules_path: str): ...

    def evaluate(self, as_of: date) -> RegimeResult: ...
```

### 8.1 규칙 파일 형식

`config/regime_rules.yaml`

```yaml
version: "2026-08-24"

layers:
  fundamental:
    weight: 0.3
    indicators:
      - code: EXPORT_YOY
        weight: 0.6
        thresholds: { danger: -5.0, safe: 5.0 }
      - code: EXPORT_SEMI_YOY
        weight: 0.4
        thresholds: { danger: -10.0, safe: 10.0 }

  daily:
    weight: 0.7
    indicators:
      - code: DEPOSIT
        weight: 0.2
        metric: change_rate
        thresholds: { danger: -2.0, safe: 2.0 }
      # ... 나머지 5종

output:
  danger_below: -0.3
  safe_above: 0.3
```

**임계값의 초기 설정은 임시값이다.** 데이터가 쌓이면 조정한다.
`market_regime.rule_version`으로 변경 전후를 구분한다.

### 8.2 결측 처리

지표 값이 없으면(월간 지표 미발표 등) 해당 지표를 제외하고
나머지 가중치를 정규화한다. 0으로 취급하지 않는다.

### 8.3 수동 override

`market_regime.is_override = TRUE`인 행이 있으면 자동 판정보다 우선한다.
override는 만료일 없이 유지되며, 해제도 수동이다.

---

## 9. Notifier

```python
class Notifier(ABC):
    @abstractmethod
    def send(self, level: str, title: str, body: str) -> bool: ...


class TelegramNotifier(Notifier): ...
```

### 9.1 중복 억제

같은 내용의 알림이 반복되면 안 된다.

- 국면 알림: **전환 시에만** 발송. 같은 국면 유지 시 무음
- 엔진 중단: 최초 1회 + 복구 시 1회
- 수집기 실패: 1시간 내 반복 시 1회

`event_log.notified` 플래그로 발송 여부를 관리한다.

---

## 10. Portal API

포털이 화면에 제공하는 엔드포인트. 인증은 Tailscale이 처리하므로 앱 인증은 없다.

### 조회

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/dashboard` | 대시보드 전체 (1회 호출로 완결) |
| GET | `/api/accounts/{id}/positions` | 포지션 |
| GET | `/api/accounts/{id}/pnl?from=&to=` | 손익 추이 |
| GET | `/api/orders?date=&account=` | 주문 이력 |
| GET | `/api/regime/current` | 현재 국면 |
| GET | `/api/regime/history?from=&to=` | 판정 이력 |
| GET | `/api/indicators` | 지표 현황 |
| GET | `/api/keywords/surge?date=` | 급등 키워드 |
| GET | `/api/messages?keyword=&from=` | 원문 조회 |
| GET | `/api/processes` | heartbeat 상태 |
| GET | `/api/usage?date=` | API/LLM 사용량 |
| GET | `/api/backtest/runs` | 백테스트 결과 목록 |

### 제어

전부 `command` 테이블에 기록하는 방식. 포털이 엔진을 직접 호출하지 않는다.

| Method | Path | 설명 |
|---|---|---|
| POST | `/api/control/engine/{name}/stop` | 엔진 정지 |
| POST | `/api/control/engine/{name}/start` | 엔진 시작 (확인 필요) |
| POST | `/api/control/halt-entry` | 신규 진입 차단 |
| POST | `/api/control/liquidate-all` | 전량 청산 (2단계 확인) |
| POST | `/api/positions/{account}/{stock}/close` | 종목별 청산 |
| POST | `/api/filters` | 제외·허용 목록 추가 |
| DELETE | `/api/filters/{id}` | 목록 삭제 |
| POST | `/api/regime/override` | 국면 수동 설정 |
| POST | `/api/keywords/merge` | 키워드 동의어 병합 |

### 10.1 제공하지 않는 것

**파라미터 변경 엔드포인트는 만들지 않는다.** (`PROJECT.md` 8.2)
조회용 `GET /api/config`는 제공하되, 쓰기는 없다.

### 10.2 위험 조작 규약

`liquidate-all`은 요청 본문에 확인 토큰을 요구한다.

```json
{ "confirm": "LIQUIDATE", "reason": "..." }
```

우발적 호출을 막기 위한 것이다. 모든 제어 요청은 `event_log`에 남긴다.

---

## 11. 설정 파일 구조

```
config/
├── accounts.yaml          자금 배분 (계좌번호는 넣지 않는다)
├── strategy_daytrade.yaml 단타 파라미터
├── strategy_swing.yaml    스윙 파라미터
├── universe.yaml          유니버스 필터 조건
├── regime_rules.yaml      국면 판정 규칙
├── sources.yaml           수집 소스
└── limits.yaml            API/LLM 상한, 리스크 한도
```

`.env`에는 비밀값만 둔다. git에 커밋하지 않는다.

```
KIWOOM_APP_KEY_PAPER=
KIWOOM_APP_SECRET_PAPER=
KIWOOM_APP_KEY_LIVE=
KIWOOM_APP_SECRET_LIVE=
KIWOOM_ACCOUNT_DAYTRADE=
KIWOOM_ACCOUNT_SWING=
KIWOOM_ACCOUNT_PAPER=
ANTHROPIC_API_KEY=
TELEGRAM_BOT_TOKEN=
DART_API_KEY=
DATABASE_URL=
```

**계좌번호는 `.env`에만 둔다.** `config/`와 DB 어디에도 두지 않는다.
`account.account_id`로 해당 환경변수를 찾는다. (`SCHEMA.md` 1장)

`config/accounts.yaml`은 비밀값을 담지 않으므로, 같은 구조의
`config/accounts.example.yaml`을 커밋해 형식을 남긴다.

---

## 12. 미결 사항

| 항목 | 비고 |
|---|---|
| 웹소켓 재연결 정책 | 끊김 시 재구독 절차 |
| `get_candles` 호출 한도 | 키움 제한 확인 후 캐시 전략 결정 |
| 백테스트 체결 모델 | 종가 체결 vs 다음 봉 시가. 스윙/단타 별도 |
| 분봉 과거 조회 가능 범위 | Phase 10 시작 시점을 좌우한다. 확인 필요 |
| 모의투자의 API 지원 범위 | 실전과 다른 부분이 있는지. Phase 8 검증 계획에 영향 |
