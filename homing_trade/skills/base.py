from abc import ABC, abstractmethod
from homing_trade.models import Candle, Position, Signal


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def on_candle(self, candles: list[Candle], position: Position | None) -> Signal:
        """Return a trading Signal given the rolling candle window and current position."""
        raise NotImplementedError
