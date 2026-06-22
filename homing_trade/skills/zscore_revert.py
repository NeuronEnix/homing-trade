"""Z-score mean-reversion — a candidate mean-reverter (Phase 7 #2).

Standardize the latest close against its rolling `period` mean/std: z = (price − mean) / std. Go LONG
when price is `z_entry` std below the mean (oversold) and flat; close a long once it reverts back
toward the mean (z ≥ −z_exit). Long-only, mirroring the Bollinger reverter (the engine supports SHORT,
but candidate reverters stay long-only for now). The window includes the current bar, matching the
Bollinger convention.

CANDIDATE: registered in the skill factory (evaluable by the backtest + walk-forward harness) but NOT
in the default enabled_skills — it joins the live paper tournament only on out-of-sample promotion.
"""
from homing_trade.skills.base import Strategy
from homing_trade.models import Signal


class ZScoreRevert(Strategy):
    name = "zscore_revert"

    def __init__(self, period=20, z_entry=2.0, z_exit=0.5):
        self.period = period
        self.z_entry = z_entry
        self.z_exit = z_exit

    def on_candle(self, candles, position):
        closes = [c.close for c in candles]
        if len(closes) < self.period:
            return Signal("HOLD", reason="warming up")
        window = closes[-self.period:]
        mean = sum(window) / self.period
        sd = (sum((v - mean) ** 2 for v in window) / self.period) ** 0.5
        if sd == 0:
            return Signal("HOLD", reason="flat", indicators={"z": 0.0, "mean": round(mean, 2)})
        z = (closes[-1] - mean) / sd
        ind = {"z": round(z, 3), "mean": round(mean, 2), "sd": round(sd, 4)}
        is_long = position is not None and position.side == "LONG"
        if position is None and z <= -self.z_entry:
            return Signal("LONG", confidence=min(0.9, abs(z) / 4),
                          reason=f"z={z:.2f} <= -{self.z_entry}", indicators=ind)
        if is_long and z >= -self.z_exit:
            return Signal("CLOSE", confidence=0.6, reason=f"z={z:.2f} reverted toward mean", indicators=ind)
        return Signal("HOLD", reason=f"z={z:.2f}", indicators=ind)
