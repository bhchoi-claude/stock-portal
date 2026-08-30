# 스윙 추세 추종 전략. DB 없이 스텁 피드로 규칙을 고정한다

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from common.config import load_config
from common.strategy.base import Context
from common.strategy.swing import SwingStrategy
from common.types import Balance, Candle, Position, Quote, Regime, Side

PARAMS = load_config("strategy_swing")
STOCK = "KRX:005930"
DAY = date(2025, 3, 3)

FLAT = Decimal(10000)


def series(closes: list[Decimal], highs: list[Decimal] | None = None) -> list[Candle]:
    """종가 목록으로 일봉을 만든다. 고가를 주지 않으면 종가와 같다."""
    highs = highs or closes
    return [
        Candle(
            stock_id=STOCK,
            ts=datetime.combine(
                DAY + timedelta(days=i), datetime.min.time(), tzinfo=UTC
            ),
            open=close,
            high=high,
            low=close,
            close=close,
            volume=0,
        )
        for i, (close, high) in enumerate(zip(closes, highs, strict=True))
    ]


class StubFeed:
    """규격만 만족하는 최소 피드. 봉과 현재가를 시험이 정한다."""

    def __init__(self, candles: list[Candle], quote: Decimal = FLAT):
        self.candles = candles
        self.quote = quote

    def now(self):
        return datetime(2025, 3, 3, 6, 30, tzinfo=UTC)

    def get_candles(self, stock_id, interval, count):
        return self.candles[-count:]

    def get_quote(self, stock_id):
        return Quote(stock_id, self.now(), self.quote, None, None, 0)

    def get_universe(self):
        return [STOCK]

    def get_regime(self):
        return Regime.NEUTRAL

    def get_signals(self, strategy, since):
        return []


def make_context(feed, positions=None) -> Context:
    return Context(
        feed=feed,
        account_id="swing",
        params=PARAMS,
        positions=positions or {},
        balance=Balance("swing", *(Decimal(0),) * 4),
    )


def rising(length: int, top: Decimal = Decimal(12000)) -> list[Decimal]:
    """마지막 봉만 확실히 신고가인 완만한 상승. 이평 위에 있게 만든다."""
    return [Decimal(10000) + Decimal(i) for i in range(length - 1)] + [top]


NEED = max(PARAMS["ma_long"], PARAMS["breakout_days"] + 1)


# --- 진입 ---------------------------------------------------------------


def test_breakout_above_the_long_ma_is_an_entry():
    intents = SwingStrategy().scan(make_context(StubFeed(series(rising(NEED)))))

    assert len(intents) == 1
    assert intents[0].side is Side.BUY
    assert intents[0].stock_id == STOCK


def test_no_entry_without_a_new_high():
    """직전 최고가를 못 넘으면 진입하지 않는다."""
    closes = rising(NEED, top=Decimal(10000))
    intents = SwingStrategy().scan(make_context(StubFeed(series(closes))))

    assert intents == []


def test_new_high_below_the_long_ma_is_not_an_entry():
    """신고가여도 장기 이평 아래면 사지 않는다. 조건이 둘이다."""
    # 길게 내려온 뒤 마지막에 반등해 직전 최고가만 살짝 넘긴다
    closes = [Decimal(20000 - i * 100) for i in range(NEED - 1)]
    closes.append(closes[-1] + Decimal(1))
    intents = SwingStrategy().scan(make_context(StubFeed(series(closes))))

    assert intents == []


def test_the_prior_high_excludes_today():
    """오늘 고가를 직전 최고가에 넣으면 무엇도 돌파가 아니게 된다."""
    closes = rising(NEED)
    # 오늘 고가만 아주 높다. 종가는 그대로다
    highs = list(closes[:-1]) + [Decimal(99999)]
    intents = SwingStrategy().scan(make_context(StubFeed(series(closes, highs))))

    assert len(intents) == 1


def test_no_entry_while_warming_up():
    """이평이 덜 찼으면 판단하지 않는다. 없는 이평을 지어내지 않는다."""
    intents = SwingStrategy().scan(make_context(StubFeed(series(rising(NEED - 1)))))

    assert intents == []


def test_held_stock_is_skipped():
    held = {STOCK: Position("swing", STOCK, 10, FLAT)}
    intents = SwingStrategy().scan(
        make_context(StubFeed(series(rising(NEED))), positions=held)
    )

    assert intents == []


def test_strength_is_the_breakout_excess():
    """돌파 폭이 강도다. 새 파라미터 없이 이미 읽은 데이터에서 나온다."""
    closes = rising(NEED, top=Decimal(20000))
    intents = SwingStrategy().scan(make_context(StubFeed(series(closes))))

    prior_high = max(closes[:-1])
    expected = (Decimal(20000) / prior_high - Decimal(1)) * 100
    assert intents[0].strength == expected
    assert intents[0].payload["reason"] == "breakout"


def test_entries_are_capped_per_day():
    class ManyFeed(StubFeed):
        def get_universe(self):
            return [f"KRX:00000{n}" for n in range(1, 9)]

    intents = SwingStrategy().scan(make_context(ManyFeed(series(rising(NEED)))))

    assert len(intents) == PARAMS["entries_per_day"]


def test_intent_has_no_quantity():
    """전략은 수량을 정하지 않는다 (INTERFACES.md 4.2)."""
    intents = SwingStrategy().scan(make_context(StubFeed(series(rising(NEED)))))

    assert not hasattr(intents[0], "quantity")


# --- 청산 ---------------------------------------------------------------


def held(avg_price: Decimal = FLAT) -> Position:
    return Position("swing", STOCK, 10, avg_price)


def test_stop_loss_uses_raw_prices():
    """`avg_price` 는 체결가 기반 원주가다. 현재가도 원주가로 견준다 (3.3)."""
    below = FLAT * (Decimal(1) - Decimal(str(PARAMS["stop_loss"])))
    feed = StubFeed(series(rising(NEED)), quote=below)

    intent = SwingStrategy().manage(make_context(feed), held())
    assert intent is not None
    assert intent.reason == "stop"
    assert intent.quantity == 10


def test_no_exit_above_the_stop_and_the_exit_ma():
    feed = StubFeed(series(rising(NEED)), quote=Decimal(12000))
    assert SwingStrategy().manage(make_context(feed), held()) is None


def test_losing_the_exit_ma_is_a_signal_exit():
    """종가가 단기 이평을 밑돌면 추세가 꺾인 것으로 본다."""
    closes = [Decimal(20000)] * (NEED - 1) + [Decimal(15000)]
    feed = StubFeed(series(closes), quote=Decimal(15000))

    intent = SwingStrategy().manage(make_context(feed), held(Decimal(15000)))
    assert intent is not None
    assert intent.reason == "signal"


def test_stop_wins_when_both_trigger():
    """둘 다 걸리면 더 구체적인 사유를 남긴다."""
    closes = [Decimal(20000)] * (NEED - 1) + [Decimal(1000)]
    feed = StubFeed(series(closes), quote=Decimal(1000))

    intent = SwingStrategy().manage(make_context(feed), held(Decimal(20000)))
    assert intent.reason == "stop"


def test_exit_ma_is_not_judged_while_warming_up():
    """이평이 덜 찼으면 추세 이탈을 판단하지 않는다."""
    short = series([Decimal(20000)] * (PARAMS["ma_exit"] - 1))
    feed = StubFeed(short, quote=FLAT)

    assert SwingStrategy().manage(make_context(feed), held()) is None


# --- 파라미터 -----------------------------------------------------------


def test_no_number_is_hardcoded():
    """코드에 숫자를 쓰지 않는다 (CLAUDE.md 1, INTERFACES.md 4.3)."""
    tightened = {**PARAMS, "stop_loss": 0.01}
    slightly_down = FLAT * Decimal("0.98")
    feed = StubFeed(series(rising(NEED)), quote=slightly_down)

    ctx = make_context(feed)
    ctx.params = tightened
    assert SwingStrategy().manage(ctx, held()).reason == "stop"

    ctx.params = {**PARAMS, "stop_loss": 0.5}
    assert SwingStrategy().manage(ctx, held()) is None
