# DataFeed 규격. 전략은 이것만 보고 돌아간다 (INTERFACES.md 3장)

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from ..types import Candle, Quote, Regime, Signal


class DataFeed(ABC):
    """백테스트와 실전이 같은 전략 코드를 쓰게 만드는 경계.

    **전략과 피드 구현체 안에서 `datetime.now()` 를 부르지 않는다.**
    반드시 `feed.now()` 를 쓴다. 어기면 백테스트가 미래를 참조하게 되고,
    가장 흔하고 가장 발견하기 어려운 버그가 된다 (INTERFACES.md 3.1).
    """

    @abstractmethod
    def now(self) -> datetime:
        """현재 시각(UTC). 백테스트에서는 시뮬레이션 시각."""

    @abstractmethod
    def get_candles(self, stock_id: str, interval: str, count: int) -> list[Candle]:
        """now() 시점까지의 봉. 미래 데이터는 절대 포함하지 않는다.

        **조정가로 준다.** 조정하지 않으면 분할일이 급락으로 보인다 (3.3).
        """

    @abstractmethod
    def get_quote(self, stock_id: str) -> Quote:
        """현재가. **조정하지 않는다.** 현재가는 조정 대상이 아니다 (3.3)."""

    @abstractmethod
    def get_universe(self) -> list[str]:
        """now() 시점의 매매 대상 종목.

        종목 상태는 `stock` 의 현재값이 아니라 `stock_status` 의 그 시점 값을
        읽는다. 폐지 종목도 폐지 이전에는 포함된다 (3.4).
        """

    @abstractmethod
    def get_regime(self) -> Regime:
        """그 시점의 시장 국면."""

    @abstractmethod
    def get_signals(self, strategy: str, since: datetime) -> list[Signal]:
        """정보수집이 만든 시그널. 참고 지표로 쓴다."""
