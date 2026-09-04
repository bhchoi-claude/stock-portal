# 스윙 엔진 상주 루프. 하루의 순서는 BacktestLoop 와 같다

from __future__ import annotations

import logging
import time as _time
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import psycopg

from common.broker.base import Broker, OrderResult
from common.broker.errors import BrokerError
from common.db.commands import Command, ack, complete, pending_commands
from common.db.conn import transaction
from common.db.events import log_event
from common.db.filters import blocked_stock_ids
from common.db.heartbeat import upsert_heartbeat
from common.db.orders import OpenOrder, apply_result, list_open_orders
from common.db.pnl import snapshot
from common.db.positions import list_positions, sync_positions
from common.db.prices import traded_range
from common.db.signals import (
    consume,
    last_planned_at,
    pending_signals,
    record_signal,
)
from common.feed.live import SEOUL, LiveFeed
from common.order import place_order
from common.risk import RiskManager
from common.strategy.base import Context, EntryIntent, Strategy
from common.types import Balance, OrderType, Position, Regime, Side, Signal

from .schedule import Timetable, due_tasks

log = logging.getLogger(__name__)


class SwingEngine:
    """24시간 상주하며 시간표대로 움직인다.

    하루의 순서가 `BacktestLoop` 와 같다. 전날 정한 주문을 시가에 내고
    (`submit`), 종가가 확정된 뒤 `manage`·`scan` 으로 다음을 정한다
    (`plan`). 그 사이가 실전에만 있는 것들이다 — 체결 추적, 미체결 취소,
    명령 폴링.

    **포털의 HTTP 응답을 기다리지 않는다.** 통신은 `heartbeat` 와
    `command` 테이블로만 한다 (CLAUDE.md 8).

    커넥션이 둘이다. `conn` 은 쓰기용이고 `feed` 는 자기 autocommit 읽기
    커넥션을 따로 들고 있다. 섞으면 남의 미커밋 작업을 지운다.
    """

    def __init__(
        self,
        *,
        conn: psycopg.Connection,
        feed: LiveFeed,
        broker: Broker,
        strategy: Strategy,
        risk: RiskManager,
        strategy_params: dict[str, Any],
        params: dict[str, Any],
        allocation: Decimal = Decimal(0),
    ) -> None:
        self.conn = conn
        self.feed = feed
        self.broker = broker
        self.strategy = strategy
        self.risk = risk
        self.strategy_params = strategy_params
        self.params = params
        self.allocation = allocation

        self.process_name: str = params["process_name"]
        self.account_id: str = params["account_id"]
        self.table = Timetable(
            submit=_hhmm(params["submit_time"]),
            cancel=_hhmm(params["cancel_time"]),
            snapshot=_hhmm(params["snapshot_time"]),
            plan=_hhmm(params["plan_time"]),
        )

        self.done: dict[str, date] = {}
        self.retry_at: dict[str, datetime] = {}
        self.plan_attempts = 0
        # 진입만 막힌 상태. 청산은 계속 돈다 (INTERFACES.md 4.1)
        self.halt_entry = False
        self.stopping = False
        self._last_beat: datetime | None = None
        self._last_track: datetime | None = None

    # --- 수명 -------------------------------------------------------------

    def recover(self) -> None:
        """매매를 시작하기 전에 증권사와 대조한다 (`INTERFACES.md` 2.2).

        **이 과정이 끝나기 전에는 신규 주문을 내지 않는다.** `run()` 이
        루프에 들어가기 전에 부른다.
        """
        self._beat("running", restart=True)
        self._reconcile_orders()
        self._sync_positions(cold_start_ok=True)

    def run(self) -> int:
        """정지 명령을 받을 때까지 돈다."""
        self.recover()
        log.info("%s 시작. 계좌 %s", self.process_name, self.account_id)

        while not self.stopping:
            try:
                self.tick(self.now())
            except Exception as exc:
                # 한 번 넘어진다고 상주 프로세스가 죽으면 안 된다
                log.exception("틱 처리 실패")
                self._event("ERROR", f"틱 처리 실패: {type(exc).__name__}: {exc}")
                self._beat("error", {"error": f"{type(exc).__name__}: {exc}"})
            # 시각 판단이 아니라 대기다 (CLAUDE.md 2 의 시스템 레벨 예외)
            _time.sleep(self.params["tick_sec"])

        self._beat("stopping")
        return 0

    def now(self) -> datetime:
        """**한국 시각.** 시간표도 거래일도 시장 현지 기준이다 (CLAUDE.md 5).

        `feed.now()` 를 통해 읽는다. 엔진이 `datetime.now()` 를 직접 부르면
        백테스트와 같은 규약이 깨진다 (`INTERFACES.md` 3.1).
        """
        return self.feed.now().astimezone(SEOUL)

    def tick(self, now: datetime) -> None:
        """한 번의 순환. **명령이 먼저고 그다음이 시간표다.**

        정지나 청산이 걸려 있는데 시간표대로 새 주문을 내면 안 된다.
        """
        self._poll_commands()
        if self.stopping:
            return

        for task in due_tasks(
            now,
            self.table,
            self.done,
            self.retry_at,
            window_min=self.params["task_window_min"],
        ):
            getattr(self, f"_{task}")(now)

        self._track_fills(now)
        self._maybe_beat(now)

    # --- 시간표의 네 가지 --------------------------------------------------

    def _submit(self, now: datetime) -> None:
        """전날 계획을 **동시호가 시장가**로 낸다.

        백테스트가 '다음 날 시가 체결' 을 가정한다. 09:00 개장 뒤에 내면
        시가가 아니라 그때 가격에 체결된다.

        **수량을 여기서 정한다.** 매수는 지금 잔고로 `RiskManager` 를
        돌리고 매도는 지금 보유 수량이다. 계획에는 수량이 없다.
        """
        self.done["submit"] = now.date()

        basis = self._plan_basis()
        if basis is None:
            log.info("일봉이 없어 제출할 계획이 없습니다")
            return

        with self.conn.cursor() as cur:
            signals = pending_signals(cur, self.strategy.name, basis)
        self.conn.rollback()  # psycopg 는 SELECT 하나에도 트랜잭션을 연다
        if not signals:
            return

        ctx = self._context()
        regime = self.feed.get_regime()
        blocked = self._blocked(now.date())
        for signal in signals:
            if signal.side is Side.BUY and signal.stock_id in blocked:
                # 어젯밤 계획한 뒤 아침에 막았을 수 있다. 마지막 순간까지 본다
                log.info("%s 제외 목록에 있어 사지 않습니다", signal.stock_id)
                self._consume(signal.signal_id)
                continue
            self._submit_one(signal, ctx, regime)

    def _cancel(self, now: datetime) -> None:
        """미체결 잔량을 취소한다.

        백테스트에 미체결이라는 상태가 없다. 시가에 못 사면 주문을 버리는
        `_settle` 과 같은 처리다. 다음 주기에 `scan` 이 다시 만든다.
        """
        self.done["cancel"] = now.date()

        for order in self._open_orders():
            if order.broker_order_no is None:
                continue
            try:
                result = self.broker.cancel_order(
                    order.account_id,
                    order.broker_order_no,
                    order.client_order_id,
                    order.stock_id,
                )
            except BrokerError as exc:
                # 방금 체결된 건이 이 경로로 온다. 드문 일이 아니라 정상
                # 흐름이라 다음 주기에 다시 본다 (context-notes 08-31 (5))
                log.info("%s 취소가 거부됐습니다: %s", order.stock_id, exc)
                continue
            self._apply(result)

    def _snapshot(self, now: datetime) -> None:
        """그날 계좌 상태를 `daily_pnl` 에 남긴다."""
        self.done["snapshot"] = now.date()

        positions = self._sync_positions()
        balance = self.broker.get_balance(self.account_id)
        with transaction(self.conn) as cur:
            snapshot(cur, trade_date=now.date(), balance=balance, positions=positions)

    def _plan(self, now: datetime) -> None:
        """`manage` 로 청산을, `scan` 으로 진입을 정해 `signal` 에 남긴다.

        `manage` 가 `scan` 보다 먼저다. 진입을 막은 상태에서도 청산은 돌아야
        한다는 규격과 같은 순서다 (`INTERFACES.md` 4.1). `BacktestLoop._plan`
        과 순서가 같다.

        **일봉이 그날치까지 쌓였는지 먼저 본다.** `get_universe()` 는 빈
        목록을 줄 뿐이라 '후보 없음' 과 구분되지 않는다. heartbeat 로는 알
        수 없다 — 프로세스가 돌았다는 뜻이지 데이터가 들어왔다는 뜻이 아니다.
        """
        if self._planned_today(now):
            # 창 안(19:00~19:30)에 재시작하면 계획이 두 번 만들어진다.
            # `done` 은 프로세스 메모리라 재시작에 살아남지 못한다
            log.info("오늘 계획을 이미 남겼습니다")
            self.done["plan"] = now.date()
            return

        if not self._daily_loaded(now.date()):
            self._defer_plan(now)
            return

        self.plan_attempts = 0
        self.retry_at.pop("plan", None)
        self.done["plan"] = now.date()

        positions = self._sync_positions()
        ctx = self._context(positions)
        regime = self.feed.get_regime()

        exits = self._plan_exits(ctx, regime)
        entries = 0 if self.halt_entry else self._plan_entries(ctx, regime)

        log.info("계획: 청산 %d건, 진입 %d건 (국면 %s)", exits, entries, regime.value)
        self._event(
            "INFO",
            f"다음 거래일 계획 {exits + entries}건",
            detail={"exits": exits, "entries": entries, "regime": regime.value},
        )

    # --- 계획 --------------------------------------------------------------

    def _plan_exits(self, ctx: Context, regime: Regime) -> int:
        """보유 포지션마다 `manage` 를 부른다. **전량 청산만 담을 수 있다.**"""
        count = 0
        for position in list(ctx.positions.values()):
            intent = self.strategy.manage(ctx, position)
            if intent is None:
                continue

            if intent.quantity != position.quantity:
                # 계획에 수량을 담지 않으므로 부분 청산을 표현할 수 없다.
                # 조용히 전량 파는 것보다 남기지 않는 쪽이 안전하다.
                # 부분 청산 전략이 생기면 여기가 먼저 걸린다
                log.error(
                    "%s 부분 청산은 아직 계획에 담을 수 없습니다 (%d/%d)",
                    position.stock_id,
                    intent.quantity,
                    position.quantity,
                )
                self._event(
                    "ERROR",
                    f"{position.stock_id} 부분 청산 의도를 계획에 담지 못했습니다",
                    detail={
                        "intent": intent.quantity,
                        "held": position.quantity,
                        "reason": intent.reason,
                    },
                )
                continue

            with transaction(self.conn) as cur:
                record_signal(
                    cur,
                    stock_id=intent.stock_id,
                    strategy=self.strategy.name,
                    side=Side.SELL,
                    payload={"reason": intent.reason},
                    regime_at=regime.value,
                )
            count += 1
        return count

    def _plan_entries(self, ctx: Context, regime: Regime) -> int:
        """`scan` 결과를 그대로 남긴다. **한도는 여기서 보지 않는다.**

        `RiskManager` 는 08:30 에 돈다. 그때의 주문가능금액과 보유 종목 수로
        판단해야 갭상승이 반영된다.
        """
        count = 0
        blocked = self._blocked(self.now().date())
        for intent in self.strategy.scan(ctx):
            if intent.stock_id in blocked:
                log.info("%s 제외 목록에 있어 계획에서 뺍니다", intent.stock_id)
                continue
            with transaction(self.conn) as cur:
                record_signal(
                    cur,
                    stock_id=intent.stock_id,
                    strategy=self.strategy.name,
                    side=intent.side,
                    strength=intent.strength,
                    payload=intent.payload,
                    regime_at=regime.value,
                )
            count += 1
        return count

    def _planned_today(self, now: datetime) -> bool:
        """오늘 이미 계획을 남겼는가. **DB 로 본다.**

        `done` 은 프로세스 메모리에만 있어 재시작하면 비고, 그러면 같은 날
        계획이 두 번 만들어진다. 같은 종목의 신호가 둘이 되면 다음 날 아침에
        주문이 두 번 나간다.
        """
        with self.conn.cursor() as cur:
            last = last_planned_at(cur, self.strategy.name)
        self.conn.rollback()
        return last is not None and last.astimezone(SEOUL).date() == now.date()

    def _defer_plan(self, now: datetime) -> None:
        """일봉이 아직 없다. 몇 번까지 기다린 뒤 그날을 건너뛴다.

        '데이터가 없어서 안 샀다' 는 안전한 실패다. 오래된 데이터로 판단하는
        것보다 낫다.
        """
        self.plan_attempts += 1
        if self.plan_attempts > self.params["plan_retry_max"]:
            log.warning("일봉이 없어 오늘 판단을 건너뜁니다")
            self._event("WARN", "일봉 미적재로 오늘 판단을 건너뜁니다")
            self.done["plan"] = now.date()
            self.plan_attempts = 0
            self.retry_at.pop("plan", None)
            return

        self.retry_at["plan"] = now + timedelta(minutes=self.params["plan_retry_min"])
        log.info(
            "일봉이 아직 없습니다. %d분 뒤 다시 봅니다 (%d/%d)",
            self.params["plan_retry_min"],
            self.plan_attempts,
            self.params["plan_retry_max"],
        )

    # --- 제출 --------------------------------------------------------------

    def _submit_one(self, signal: Signal, ctx: Context, regime: Regime) -> None:
        """계획 한 건을 주문으로 바꾼다. 수량이 안 나오면 소비만 하고 만다."""
        quantity = self._quantity_for(signal, ctx, regime)
        if quantity is None:
            self._consume(signal.signal_id)
            return

        try:
            result = place_order(
                self.conn,
                self.broker,
                account_id=self.account_id,
                stock_id=signal.stock_id,
                side=signal.side,
                order_type=OrderType.MARKET,
                quantity=quantity,
                signal_id=signal.signal_id,
            )
        except Exception as exc:
            # 접수됐는지 모른다. **재시도하지 않는다** (CLAUDE.md 3).
            # 행은 pending 으로 남고 다음 재시작 복구가 대조한다
            log.exception("%s 주문 응답을 받지 못했습니다", signal.stock_id)
            self._event(
                "ERROR",
                f"{signal.stock_id} 주문 응답 없음. 접수 여부를 확인해야 합니다",
                detail={"error": f"{type(exc).__name__}: {exc}"},
            )
            self.halt_entry = True
            return

        log.info(
            "%s %s %d주 → %s",
            signal.side.value,
            signal.stock_id,
            quantity,
            result.status,
        )

    def _quantity_for(self, signal: Signal, ctx: Context, regime: Regime) -> int | None:
        """이 계획으로 몇 주를 낼지. 낼 수 없으면 `None`.

        매도는 지금 보유 수량이다. `SwingStrategy.manage` 가 전량 청산만
        내고, 부분 청산은 `_plan_exits` 가 계획에 담지 않는다.
        """
        if signal.side is Side.SELL:
            position = ctx.positions.get(signal.stock_id)
            if position is None:
                # 이미 없는 종목이다. 계획만 닫는다
                return None
            return position.quantity

        if self.halt_entry:
            log.info("%s 진입이 막혀 있어 건너뜁니다", signal.stock_id)
            return None

        intent = EntryIntent(
            stock_id=signal.stock_id,
            side=Side.BUY,
            strength=signal.strength or Decimal(0),
        )
        decision = self.risk.evaluate(intent, ctx, regime)
        if not decision.approved:
            log.info("%s 진입 거부: %s", signal.stock_id, decision.reason)
            return None
        return decision.quantity

    # --- 장중 --------------------------------------------------------------

    def _track_fills(self, now: datetime) -> None:
        """미체결 주문의 상태를 갱신한다. 장중에만, 띄엄띄엄 본다.

        `get_order_status` 는 목록 API 둘(`ka10075`·`ka10076`)을 부른다.
        주문마다 매 틱 부르면 api-id 별 유량(초당 1건, 2026-08-31 실측)에
        바로 닿는다. `fill_poll_sec` 간격을 둔다.
        """
        if not _hhmm(self.params["track_from"]) <= now.time() < self.table.cancel:
            return
        if not self._elapsed(self._last_track, now, self.params["fill_poll_sec"]):
            return
        self._last_track = now

        filled = False
        for order in self._open_orders():
            if order.broker_order_no is None:
                continue
            result = self._status_of(order)
            self._apply(result)
            if result.status != order.status and result.filled_qty > 0:
                filled = True

        if filled:
            # **체결됐으면 포지션을 바로 맞춘다.** 안 맞추면 15:40 대조에서
            # 우리가 낸 주문의 결과가 '원장 불일치' 로 잡힌다.
            # 보고하지 않는다 — 이 차이는 우리가 만든 것이다
            self._sync_positions(report=False)

    # --- 명령 --------------------------------------------------------------

    def _poll_commands(self) -> None:
        """포털이 넣은 명령을 처리한다. **받았다고 먼저 찍는다.**

        순서가 바뀌면 처리 중에 죽었을 때 다음 폴링이 같은 명령을 또 집는다.
        전량청산이 두 번 도는 것이 그 사고다.
        """
        with self.conn.cursor() as cur:
            commands = pending_commands(cur, self.process_name)
        self.conn.rollback()

        for command in commands:
            with transaction(self.conn) as cur:
                ack(cur, command.command_id)
            try:
                result, ok = self._handle(command), True
            except Exception as exc:
                log.exception("명령 처리 실패: %s", command.action)
                result, ok = f"{type(exc).__name__}: {exc}", False
            with transaction(self.conn) as cur:
                complete(cur, command.command_id, ok=ok, result=result)

    def _handle(self, command: Command) -> str:
        """명령 하나를 처리하고 화면에 보일 결과 문구를 돌려준다."""
        if command.action == "stop":
            self.stopping = True
            return "정지합니다"

        if command.action == "halt_entry":
            self.halt_entry = True
            self._event("WARN", "신규 진입을 차단했습니다")
            return "신규 진입을 차단했습니다"

        if command.action == "liquidate_all":
            count = sum(self._liquidate(p) for p in self._sync_positions())
            # 청산해놓고 다음 주기에 다시 사면 안 된다
            self.halt_entry = True
            self._event("WARN", f"전량 청산 주문 {count}건을 냈습니다")
            return f"청산 주문 {count}건"

        if command.action == "close_position":
            stock_id = (command.params or {}).get("stock_id")
            held = {p.stock_id: p for p in self._sync_positions()}
            if stock_id not in held:
                return f"보유하지 않은 종목입니다: {stock_id}"
            return f"청산 주문 {self._liquidate(held[stock_id])}건"

        raise ValueError(f"모르는 명령입니다: {command.action}")

    def _liquidate(self, position: Position) -> int:
        """한 종목을 시장가로 전량 판다. 낸 주문 수를 돌려준다.

        비상 경로라 계획(`signal`)을 거치지 않는다. 한 종목이 실패해도
        나머지는 계속 판다 — 비상 중단이 한 종목 때문에 멈추면 안 된다.
        """
        try:
            place_order(
                self.conn,
                self.broker,
                account_id=self.account_id,
                stock_id=position.stock_id,
                side=Side.SELL,
                order_type=OrderType.MARKET,
                quantity=position.quantity,
            )
        except Exception:
            log.exception("%s 청산 주문 실패", position.stock_id)
            return 0
        return 1

    # --- 대조 --------------------------------------------------------------

    def _reconcile_orders(self) -> None:
        """끝나지 않은 주문을 증권사 상태와 맞춘다 (`INTERFACES.md` 2.2).

        주문번호가 없는 행은 **응답을 받지 못한 주문**이다. 증권사는 우리
        `client_order_id` 를 모르므로 조회로 찾을 방법이 없다. 재시도하지
        않고(CLAUDE.md 3) 진입을 막은 뒤 사람이 보게 남긴다.
        """
        unknown = []
        for order in self._open_orders():
            if order.broker_order_no is None:
                unknown.append(order.client_order_id)
                continue
            try:
                self._apply(self._status_of(order))
            except BrokerError as exc:
                # 증권사가 모르는 주문이다. **여기서 예외가 올라가면 엔진이
                # 기동조차 못 한다** — recover() 는 run() 의 예외 처리 밖이다
                self._close_vanished(order, exc)

        if unknown:
            self.halt_entry = True
            log.error("접수 여부를 모르는 주문 %d건", len(unknown))
            self._event(
                "ERROR",
                f"접수 여부를 모르는 주문 {len(unknown)}건. 확인이 필요합니다",
                detail={"client_order_ids": unknown},
            )

    def _close_vanished(self, order: OpenOrder, exc: BrokerError) -> None:
        """증권사 목록에서 사라진 주문을 닫는다.

        **취소된 주문은 어느 목록에도 안 남는다** (2026-09-03 실측). 체결
        목록(`ka10076`)도 당일 것만 주므로 **날이 바뀌면 어제 주문을 조회할
        수 없다.** 둘 다 `get_order_status` 가 `PermanentError` 를 던진다.

        닫지 않고 두면 재시작할 때마다 같은 행에서 같은 예외가 난다.
        `recover()` 는 `run()` 의 예외 처리 밖이라 **엔진이 시작하자마자
        죽고**, systemd 가 다섯 번 시도한 뒤 포기한다.

        `cancelled` 로 닫는다. 실제로 체결됐을 가능성이 남지만 **보유 수량은
        브로커 잔고가 정본이다** (`SCHEMA.md` 5장) — `_sync_positions` 가
        바로 뒤에 맞추고, 어긋나면 진입을 막는다. 주문 행 하나 때문에
        엔진을 못 띄우는 것보다 낫다.

        `filled_qty` 는 줄지 않는다. `apply_result` 의 `GREATEST` 가 막는다.
        """
        log.warning("%s 증권사가 모르는 주문이다: %s", order.stock_id, exc)
        self._apply(
            OrderResult(
                client_order_id=order.client_order_id,
                broker_order_no=order.broker_order_no,
                status="cancelled",
                filled_qty=0,
                avg_fill_price=None,
                error_message=f"증권사 조회에서 사라졌다: {exc}",
            )
        )
        self._event(
            "WARN",
            f"{order.stock_id} 주문이 증권사 조회에서 사라져 취소로 닫았습니다",
            detail={
                "client_order_id": order.client_order_id,
                "broker_order_no": order.broker_order_no,
                "was": order.status,
            },
        )

    def _sync_positions(
        self, *, cold_start_ok: bool = False, report: bool = True
    ) -> list[Position]:
        """증권사 잔고로 DB 를 맞춘다. 어긋나면 **진입만** 막는다.

        청산은 막지 않는다. 손절해야 할 종목이 원장 불일치로 묶이면 그쪽이
        더 위험하다 (`INTERFACES.md` 4.1).

        `cold_start_ok` 는 시작 시 캐시가 비어 있는 경우다. **빈 캐시는
        '어긋났다' 가 아니라 '모른다' 이므로** 진입을 막지 않는다.

        `report=False` 는 **우리가 낸 주문이 방금 체결됐을 때**다. 그 차이는
        원장이 어긋난 것이 아니라 우리가 만든 것이다. 맞추기만 하고 넘어간다.

        2026-09-04 첫 자동매매에서 이것이 없어 사고가 났다. 09:00 에 삼성전자를
        팔고 138040 을 샀는데, `position` 을 아무도 안 고쳐서 15:40 대조가
        그 둘을 불일치로 잡고 진입을 막았다. **엔진이 자기 행동의 결과를
        이상 징후로 읽었다.** 그래서 19:00 계획이 0건이 됐다.

        **매매 없이 어긋난 것이 진짜 신호다.**
        """
        positions = self.broker.get_positions(self.account_id)
        with transaction(self.conn) as cur:
            mismatches = sync_positions(cur, self.account_id, positions)

        tolerance = self.params["position_tolerance"]
        real = [
            m
            for m in mismatches
            if abs(m.db_quantity - m.broker_quantity) > tolerance
            and not (cold_start_ok and m.db_quantity == 0)
        ]
        if real and not report:
            log.info("체결 반영: %d종목", len(real))
        elif real:
            self.halt_entry = True
            log.error("잔고 불일치 %d종목. 진입을 막습니다", len(real))
            self._event(
                "ERROR",
                f"잔고 불일치 {len(real)}종목. 증권사 값으로 맞추고 진입을 막았습니다",
                detail={
                    m.stock_id: {"db": m.db_quantity, "broker": m.broker_quantity}
                    for m in real
                },
            )
        return positions

    # --- 보조 --------------------------------------------------------------

    def _context(self, positions: list[Position] | None = None) -> Context:
        """전략과 `RiskManager` 가 볼 것. 잔고는 증권사에서 온다.

        `allocation` 이 양수면 총자산을 그 금액으로 제한한다. 0 이면 브로커
        잔고를 그대로 쓴다 (`accounts.yaml`).

        **총자산만 제한하면 된다.** `RiskManager._budget` 이 국면 배분에서
        이미 보유 금액을 빼므로, 배분 한도가 그대로 총 투입 상한이 된다.
        """
        if positions is None:
            with self.conn.cursor() as cur:
                positions = list_positions(cur, self.account_id)
            self.conn.rollback()

        balance = self.broker.get_balance(self.account_id)
        if self.allocation > 0:
            balance = Balance(
                account_id=balance.account_id,
                deposit=balance.deposit,
                available=balance.available,
                eval_amount=balance.eval_amount,
                total_asset=min(balance.total_asset, self.allocation),
                currency=balance.currency,
            )

        self.risk.start_day(balance.total_asset)
        return Context(
            feed=self.feed,
            account_id=self.account_id,
            params=self.strategy_params,
            positions={p.stock_id: p for p in positions},
            balance=balance,
        )

    def _plan_basis(self) -> datetime | None:
        """제출할 계획의 하한 시각. **계획을 만든 일봉이 최신일 때만 유효하다.**

        마지막 거래일의 `plan_time` 을 돌려준다. 그보다 오래된 계획은
        `pending_signals` 가 걸러낸다.

        **공휴일이 이것으로 처리된다.** 월요일이 휴장이면 화요일 아침에도
        마지막 일봉이 여전히 금요일치라 금요일 저녁 계획이 그대로 유효하다.
        반대로 엔진이 이틀 쉬었으면 최신 일봉이 넘어가 있어 낡은 계획이
        자동으로 걸러진다.

        달력을 쓰지 않는 이유는 `exchange_holiday` 가 일봉에서 역산해
        채워져 **앞으로의 휴일을 모르기** 때문이다.
        """
        with self.conn.cursor() as cur:
            spanned = traded_range(cur)
        self.conn.rollback()
        if spanned is None:
            return None
        return datetime.combine(spanned[1], self.table.plan, tzinfo=SEOUL)

    def _daily_loaded(self, day: date) -> bool:
        """일봉이 **너무 묵지 않았는지** 직접 본다.

        heartbeat 를 보지 않는다. 프로세스가 돌았다는 뜻이지 데이터가
        들어왔다는 뜻이 아니다.

        **'오늘 일봉이 있는가' 를 물으면 안 된다.** KRX 는 D일 데이터를
        D+1 에 공개하므로 19:00 수집기가 채우는 것은 어제 것이다. 오늘을
        요구하면 매일 판단을 건너뛴다 (2026-09-03 에 확인했다).

        묵은 정도로 본다. 정상은 하루(월요일이면 금요일 종가라 사흘)다.
        수집기가 며칠 멈춰 있으면 그때는 건너뛰는 것이 맞다 — 오래된
        데이터로 판단하는 것보다 낫다.
        """
        with self.conn.cursor() as cur:
            spanned = traded_range(cur)
        self.conn.rollback()
        if spanned is None:
            return False
        return (day - spanned[1]).days <= self.params["plan_max_stale_days"]

    def _blocked(self, day: date) -> set[str]:
        """오늘 사면 안 되는 종목 (`stock_filter`).

        **진입에만 쓴다.** 청산은 보지 않는다 — 들고 있는 종목을 제외
        목록에 넣었다고 팔지 못하면 갇힌다.

        전략이 아니라 엔진이 본다. 필터는 운영 판단이라 전략이 알 필요가
        없다 (`INTERFACES.md` 4.2 와 같은 결).
        """
        with self.conn.cursor() as cur:
            blocked = blocked_stock_ids(cur, self.strategy.name, day)
        self.conn.rollback()
        return blocked

    def _open_orders(self) -> list[OpenOrder]:
        with self.conn.cursor() as cur:
            orders = list_open_orders(cur, self.account_id)
        self.conn.rollback()
        return orders

    def _status_of(self, order: OpenOrder) -> OrderResult:
        assert order.broker_order_no is not None  # 호출부가 걸러낸다
        return self.broker.get_order_status(
            order.account_id, order.broker_order_no, order.client_order_id
        )

    def _apply(self, result: OrderResult) -> None:
        with transaction(self.conn) as cur:
            apply_result(cur, result)

    def _consume(self, signal_id: int) -> None:
        with transaction(self.conn) as cur:
            consume(cur, signal_id)

    def _event(
        self, level: str, message: str, detail: dict[str, Any] | None = None
    ) -> None:
        with transaction(self.conn) as cur:
            log_event(
                cur,
                self.process_name,
                level,
                message,
                category="trade",
                detail=detail,
            )

    def _maybe_beat(self, now: datetime) -> None:
        if not self._elapsed(self._last_beat, now, self.params["heartbeat_sec"]):
            return
        self._last_beat = now
        self._beat("running")

    def _beat(
        self,
        status: str,
        detail: dict[str, Any] | None = None,
        *,
        restart: bool = False,
    ) -> None:
        """생존 신호.

        **여기서 예외를 삼킨다.** 기록 대상이 DB 라 `event_log` 에도 남길 수
        없고, 신호를 못 남기는 것이 매매를 막아서는 안 된다.
        """
        try:
            with transaction(self.conn) as cur:
                upsert_heartbeat(
                    cur,
                    self.process_name,
                    status,
                    detail=detail or {"halt_entry": self.halt_entry},
                    restart=restart,
                )
        except Exception:
            log.exception("heartbeat 기록 실패")

    @staticmethod
    def _elapsed(last: datetime | None, now: datetime, seconds: float) -> bool:
        """`last` 로부터 `seconds` 가 지났는가. 처음이면 참이다."""
        return last is None or (now - last).total_seconds() >= seconds


def _hhmm(value: str) -> time:
    """`"08:30"` 을 `time` 으로. 설정 파일이 문자열로 준다."""
    hour, minute = value.split(":")
    return time(int(hour), int(minute))
