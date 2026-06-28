# homing_trade/skills/grid.py
from homing_trade.skills.base import Strategy
from homing_trade.models import Candle, Position, Signal


class Grid(Strategy):
    name = "grid"

    def __init__(self, levels: int = 5, band_pct: float = 0.02, lookback: int = 60):
        self.levels = levels
        self.band_pct = band_pct
        self.lookback = lookback

    def on_candle(self, candles: list[Candle], position: Position | None) -> Signal:
        if len(candles) < self.lookback:
            return Signal(action="HOLD", reason="warming up")
        window = candles[-self.lookback:]
        ref = sum(c.close for c in window) / len(window)
        lower = ref * (1 - self.band_pct)
        upper = ref * (1 + self.band_pct)
        price = candles[-1].close
        ind = {"ref": round(ref, 2), "lower": round(lower, 2), "upper": round(upper, 2),
               "price": round(price, 2)}
        side = position.side if position is not None else None
        # Exit at the opposite edge of the band — checked before entries (entries require flat).
        if side == "LONG" and price >= upper:
            return Signal(action="CLOSE", confidence=0.5,
                          reason=f"price {price:.2f} at/above grid top {upper:.2f}", indicators=ind)
        if side == "SHORT" and price <= lower:
            return Signal(action="CLOSE", confidence=0.5,
                          reason=f"price {price:.2f} at/below grid bottom {lower:.2f}", indicators=ind)
        if position is None and price <= lower:
            return Signal(action="LONG", confidence=0.5,
                          reason=f"price {price:.2f} at/below grid bottom {lower:.2f}", indicators=ind)
        if position is None and price >= upper:
            return Signal(action="SHORT", confidence=0.5,
                          reason=f"price {price:.2f} at/above grid top {upper:.2f}", indicators=ind)
        return Signal(action="HOLD", reason="inside band", indicators=ind)
