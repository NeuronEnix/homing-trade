from homing_trade.skills.base import Strategy
from homing_trade.models import Candle, Position, Signal


class DonchianBreakout(Strategy):
    """Donchian channel breakout. LONG when price breaks above the highest high of the prior
    `period` candles; CLOSE when it breaks below the lowest low. A classic trend/breakout system."""

    name = "donchian"

    def __init__(self, period=20):
        self.period = period

    def on_candle(self, candles, position):
        if len(candles) < self.period + 1:
            return Signal("HOLD", reason="warming up")
        prior = candles[-(self.period + 1):-1]   # the prior `period` candles, excluding current
        hi = max(c.high for c in prior)
        lo = min(c.low for c in prior)
        price = candles[-1].close
        ind = {"upper": round(hi, 2), "lower": round(lo, 2), "price": round(price, 2)}
        is_long = position is not None and position.side == "LONG"
        if position is None and price > hi:
            return Signal("LONG", confidence=0.6,
                          reason=f"breakout: price {price:.0f} > {self.period}-bar high {hi:.0f}", indicators=ind)
        if is_long and price < lo:
            return Signal("CLOSE", confidence=0.6,
                          reason=f"breakdown: price {price:.0f} < {self.period}-bar low {lo:.0f}", indicators=ind)
        return Signal("HOLD", reason="inside channel", indicators=ind)
