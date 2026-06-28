# homing_trade/skills/rsi_revert.py
from homing_trade.skills.base import Strategy
from homing_trade.skills.indicators import rsi
from homing_trade.models import Candle, Position, Signal


class RsiRevert(Strategy):
    name = "rsi_revert"

    def __init__(self, period: int = 14, oversold: float = 30, overbought: float = 70):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def on_candle(self, candles: list[Candle], position: Position | None) -> Signal:
        closes = [c.close for c in candles]
        value = rsi(closes, self.period)
        if value is None:
            return Signal(action="HOLD", reason="warming up")
        ind = {"rsi": round(value, 2)}
        side = position.side if position is not None else None
        # Exit (revert toward neutral) — check before entries; entries require a flat book.
        if side == "LONG" and value > self.overbought:
            return Signal(action="CLOSE", confidence=0.6,
                          reason=f"RSI {value:.1f} > {self.overbought} (overbought)", indicators=ind)
        if side == "SHORT" and value < self.oversold:
            return Signal(action="CLOSE", confidence=0.6,
                          reason=f"RSI {value:.1f} < {self.oversold} (oversold)", indicators=ind)
        # Entries from flat: fade the extreme on either side (symmetric mean-reversion).
        if position is None and value < self.oversold:
            return Signal(action="LONG", confidence=0.6,
                          reason=f"RSI {value:.1f} < {self.oversold} (oversold)", indicators=ind)
        if position is None and value > self.overbought:
            return Signal(action="SHORT", confidence=0.6,
                          reason=f"RSI {value:.1f} > {self.overbought} (overbought)", indicators=ind)
        return Signal(action="HOLD", reason="no extreme", indicators=ind)
