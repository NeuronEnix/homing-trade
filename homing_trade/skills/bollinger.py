from homing_trade.skills.base import Strategy
from homing_trade.skills.indicators import bollinger
from homing_trade.models import Candle, Position, Signal


class BollingerRevert(Strategy):
    """Bollinger Band mean-reversion. Buy when price closes at/below the lower band
    (oversold), exit when it reverts back up to the middle band (the mean)."""

    name = "bollinger"

    def __init__(self, period=20, num_std=2.0):
        self.period = period
        self.num_std = num_std

    def on_candle(self, candles, position):
        closes = [c.close for c in candles]
        mid, upper, lower = bollinger(closes, self.period, self.num_std)
        if mid is None:
            return Signal("HOLD", reason="warming up")
        price = closes[-1]
        ind = {"mid": round(mid, 2), "upper": round(upper, 2), "lower": round(lower, 2),
               "price": round(price, 2)}
        is_long = position is not None and position.side == "LONG"
        if position is None and price <= lower:
            return Signal("LONG", confidence=0.5,
                          reason=f"price {price:.0f} <= lower band {lower:.0f} (oversold)", indicators=ind)
        if is_long and price >= mid:
            return Signal("CLOSE", confidence=0.5,
                          reason=f"price {price:.0f} reverted to mean {mid:.0f}", indicators=ind)
        return Signal("HOLD", reason="inside bands", indicators=ind)
