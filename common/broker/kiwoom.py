# 키움 REST 어댑터. 조회만 구현한다. 주문은 Phase 8 이다

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from ..env import require_env
from ..types import Balance, Candle, InvestorFlow, Position, Quote
from .base import Broker, OrderRequest, OrderResult
from .errors import PermanentError, RateLimitError, TransientError

logger = logging.getLogger(__name__)

SEOUL = ZoneInfo("Asia/Seoul")

PAPER_DOMAIN = "https://mockapi.kiwoom.com"
LIVE_DOMAIN = "https://api.kiwoom.com"

# tic_scope 는 분 단위 숫자다. 일봉은 별도 api-id 를 쓴다
MINUTE_INTERVALS = {"1m": "1", "3m": "3", "5m": "5", "10m": "10", "30m": "30"}

CHART_PATH = "/api/dostk/chart"
INFO_PATH = "/api/dostk/stkinfo"
MINUTE_API = "ka10080"
QUOTE_API = "ka10001"
FLOW_API = "ka10059"

# ka10059 순매수 금액은 백만원 단위다 (2026-08-28 실측)
FLOW_UNIT = 1_000_000

TIMEOUT = 20.0

# 만료 이 시간 전에 토큰을 새로 받는다
TOKEN_MARGIN_SEC = 600


def strip_sign(value: str) -> Decimal:
    """키움 가격의 전일대비 부호를 벗긴다.

    분봉은 -257000 처럼 부호가 붙어 온다. 음수 가격이 아니라 방향 표시다.
    그대로 Decimal 로 읽으면 음수 가격이 저장된다 (2026-08-28 실측).
    """
    return abs(Decimal(value))


def to_utc(stamp: str) -> datetime:
    """YYYYMMDDHHMMSS 를 UTC 로 바꾼다. 원본은 KST 다."""
    naive = datetime.strptime(stamp, "%Y%m%d%H%M%S")  # noqa: DTZ007
    return naive.replace(tzinfo=SEOUL).astimezone(UTC)


class KiwoomBroker(Broker):
    """키움 REST 조회 어댑터.

    stock_id 에서 거래소 접두어를 떼는 변환이 여기 있다. 밖으로 새면
    전략이 종목코드 형식을 알게 된다 (INTERFACES.md 0장).
    """

    name = "kiwoom"

    def __init__(
        self, *, is_paper: bool, min_interval: float = 1.0, max_attempts: int = 3
    ) -> None:
        # 실전과 모의는 앱키가 별도다. 도메인과 키를 함께 골라야 한다
        suffix = "PAPER" if is_paper else "LIVE"
        self._domain = PAPER_DOMAIN if is_paper else LIVE_DOMAIN
        self._app_key = require_env("KIWOOM_APP_KEY_" + suffix)
        self._secret = require_env("KIWOOM_APP_SECRET_" + suffix)

        self._token: str | None = None
        self._expires_at: datetime | None = None
        self._min_interval = min_interval
        self._max_attempts = max_attempts
        self._last_call: dict[str, float] = {}
        self._lock = threading.Lock()

    # ---- 인증 ----

    def _fetch_token(self) -> None:
        body = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "secretkey": self._secret,
        }
        data = self._request("/oauth2/token", {}, body)
        self._token = data["token"]
        # expires_dt 는 YYYYMMDDHHMMSS 이고 KST 로 관측됐다 (2026-08-25)
        self._expires_at = to_utc(data["expires_dt"])

    def _auth_header(self) -> dict[str, str]:
        """토큰을 캐시하고 만료 전에 갱신한다. 매 호출마다 발급받지 않는다."""
        # 인증 갱신 판단은 전략이 아니라 시스템 레벨이라 실제 시각을 읽는다
        now = datetime.now(UTC)
        stale = (
            self._token is None
            or self._expires_at is None
            or (self._expires_at - now).total_seconds() < TOKEN_MARGIN_SEC
        )
        if stale:
            self._fetch_token()
        return {"authorization": "Bearer " + str(self._token)}

    # ---- HTTP ----

    def _throttle(self, api_id: str) -> None:
        """같은 api-id 는 최소 간격을 둔다. 유량이 1 이라 연속 호출은 429 다.

        직전 호출이 **끝난** 시각부터 잰다. 시작 시각부터 재면 응답 시간만큼
        간격이 줄어 429 가 난다 (2026-08-28 연속조회에서 겪었다).
        """
        with self._lock:
            last = self._last_call.get(api_id)
            if last is not None:
                wait = self._min_interval - (time.monotonic() - last)
                if wait > 0:
                    time.sleep(wait)

    def _request(
        self, path: str, headers: dict[str, str], body: dict[str, Any]
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            self._domain + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json;charset=UTF-8", **headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                data = json.load(response)
                raw_headers = dict(response.headers)
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise TransientError("키움 호출 실패: " + type(exc).__name__) from exc

        # HTTP 200 이어도 본문의 return_code 를 봐야 한다 (2026-08-25 실측)
        code = data.get("return_code")
        if code not in (0, None):
            raise PermanentError(
                "키움이 거부했습니다 ({}): {}".format(code, data.get("return_msg"))
            )
        data["_headers"] = raw_headers
        return data

    @staticmethod
    def _http_error(exc: urllib.error.HTTPError) -> Exception:
        if exc.code == 429:
            return RateLimitError("키움 호출 한도를 넘었습니다.")
        if exc.code in (401, 403):
            # 인증·권한 문제는 재시도로 풀리지 않는다
            return PermanentError(f"키움 인증 실패 (HTTP {exc.code}).")
        if exc.code >= 500:
            return TransientError(f"키움 서버 오류 (HTTP {exc.code}).")
        return PermanentError(f"키움 호출 거부 (HTTP {exc.code}).")

    def _call(
        self, api_id: str, path: str, body: dict[str, Any], **extra: str
    ) -> dict[str, Any]:
        """조회 한 번. TransientError 만 지수 백오프로 다시 건다.

        주문은 이 경로를 쓰지 않는다. 주문은 자동 재시도하지 않는다
        (INTERFACES.md 2.1).
        """
        for attempt in range(1, self._max_attempts + 1):
            self._throttle(api_id)
            headers = {**self._auth_header(), "api-id": api_id, **extra}
            try:
                return self._request(path, headers, body)
            except TransientError as exc:
                if attempt == self._max_attempts:
                    raise
                delay = getattr(exc, "retry_after", 1.0) * attempt
                logger.warning(
                    "%s 재시도 %d/%d (%s), %.1f초 대기",
                    api_id,
                    attempt,
                    self._max_attempts,
                    exc,
                    delay,
                )
                time.sleep(delay)
            finally:
                # 다음 호출은 이 호출이 끝난 시각부터 간격을 잰다
                self._last_call[api_id] = time.monotonic()
        raise AssertionError("도달할 수 없다")

    # ---- 조회 ----

    @staticmethod
    def to_code(stock_id: str) -> str:
        """KRX:005930 에서 005930 을 뽑는다.

        키움은 접두어를 붙이면 return_code = 5 로 거부한다 (2026-08-25 실측).
        """
        return stock_id.rpartition(":")[2]

    def get_quote(self, stock_id: str) -> Quote:
        data = self._call(QUOTE_API, INFO_PATH, {"stk_cd": self.to_code(stock_id)})
        # 현재가는 조정하지 않는다 (INTERFACES.md 3.3)
        return Quote(
            stock_id=stock_id,
            ts=datetime.now(UTC),
            price=strip_sign(data["cur_prc"]),
            bid=None,
            ask=None,
            volume=int(data.get("trde_qty") or 0),
        )

    def get_candles(
        self,
        stock_id: str,
        interval: str,
        count: int,
        end: datetime | None = None,
    ) -> list[Candle]:
        if interval not in MINUTE_INTERVALS:
            raise PermanentError("지원하지 않는 주기입니다: " + interval)

        body = {
            "stk_cd": self.to_code(stock_id),
            "tic_scope": MINUTE_INTERVALS[interval],
            # 일봉을 원주가로 저장하므로 분봉도 같은 기준을 쓴다
            "upd_stkpc_tp": "0",
        }
        candles: list[Candle] = []
        extra: dict[str, str] = {}

        while True:
            data = self._call(MINUTE_API, CHART_PATH, body, **extra)
            rows = data.get("stk_min_pole_chart_qry") or []
            candles += [self._to_candle(stock_id, row) for row in rows]

            headers = data["_headers"]
            # 응답이 최신순이라 필요한 개수를 채우면 더 받을 이유가 없다
            if len(candles) >= count or headers.get("cont-yn") != "Y":
                break
            extra = {"cont-yn": "Y", "next-key": headers.get("next-key", "")}

        if end is not None:
            candles = [c for c in candles if c.ts <= end]
        # 시간 오름차순으로 돌려준다 (INTERFACES.md 2장)
        candles.sort(key=lambda c: c.ts)
        return candles[-count:]

    @staticmethod
    def _to_candle(stock_id: str, row: dict[str, str]) -> Candle:
        return Candle(
            stock_id=stock_id,
            ts=to_utc(row["cntr_tm"]),
            open=strip_sign(row["open_pric"]),
            high=strip_sign(row["high_pric"]),
            low=strip_sign(row["low_pric"]),
            close=strip_sign(row["cur_prc"]),
            volume=int(row["trde_qty"]),
        )

    def get_investor_flow(self, stock_id: str, end: date) -> list[InvestorFlow]:
        """투자자별 순매수. Broker 규격 밖의 키움 전용 조회다.

        한 번에 100 거래일이 온다. `end` 가 마지막 날이다.

        **부호를 벗기면 안 된다.** 분봉 가격과 달리 여기서는 마이너스가
        순매도라는 뜻이다. 같은 API 제공자라도 필드마다 의미가 다르다.
        """
        data = self._call(
            FLOW_API,
            INFO_PATH,
            {
                "dt": end.strftime("%Y%m%d"),
                "stk_cd": self.to_code(stock_id),
                "amt_qty_tp": "1",
                "trde_tp": "0",
                "unit_tp": "1",
            },
        )
        rows = data.get("stk_invsr_orgn") or []
        return [self._to_flow(stock_id, row) for row in rows]

    @staticmethod
    def _to_flow(stock_id: str, row: dict[str, str]) -> InvestorFlow:
        return InvestorFlow(
            stock_id=stock_id,
            trade_date=date.fromisoformat(row["dt"]),
            foreign_net=Decimal(row["frgnr_invsr"]) * FLOW_UNIT,
            institution_net=Decimal(row["orgn"]) * FLOW_UNIT,
            individual_net=Decimal(row["ind_invsr"]) * FLOW_UNIT,
        )

    def get_balance(self, account_id: str) -> Balance:
        raise NotImplementedError("잔고 조회는 아직 구현하지 않았다.")

    def get_positions(self, account_id: str) -> list[Position]:
        raise NotImplementedError("보유종목 조회는 아직 구현하지 않았다.")

    # ---- 주문 (Phase 8) ----

    def submit_order(self, req: OrderRequest) -> OrderResult:
        raise NotImplementedError("주문은 Phase 8 이다.")

    def cancel_order(self, account_id: str, broker_order_no: str) -> OrderResult:
        raise NotImplementedError("주문은 Phase 8 이다.")

    def get_order_status(self, account_id: str, broker_order_no: str) -> OrderResult:
        raise NotImplementedError("주문은 Phase 8 이다.")

    # ---- 실시간 (Phase 8) ----

    def subscribe(
        self, stock_ids: list[str], on_quote: Callable[[Quote], None]
    ) -> None:
        raise NotImplementedError("웹소켓 구독은 Phase 8 이다.")

    def unsubscribe(self, stock_ids: list[str]) -> None:
        raise NotImplementedError("웹소켓 구독은 Phase 8 이다.")
