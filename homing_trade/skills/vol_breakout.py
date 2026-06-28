"""Volume-confirmed breakout — a candidate breakout strategy (Phase 7 #2).

A Donchian-style breakout that only fires when the breakout candle's volume confirms it: go LONG when
price breaks above the prior `period`-bar high AND current volume ≥ `vol_mult` × the average volume of
those prior bars (a genuine, participation-backed break, not a thin-volume fakeout). SHORT a
volume-confirmed break below the prior `period`-bar low. Symmetric (the engine closes-and-flips).

CANDIDATE: registered in the skill factory (evaluable by the backtest + walk-forward harness) but NOT
in the default enabled_skills — it joins the live paper tournament only on out-of-sample promotion.
"""
from homing_trade.skills.base import Strategy
from homing_trade.models import Signal


class VolumeBreakout(Strategy):
    name = "vol_breakout"

    def __init__(self, period=20, vol_mult=1.5):
        self.period = period
        self.vol_mult = vol_mult

    def on_candle(self, candles, position):
        if len(candles) < self.period + 1:
            return Signal("HOLD", reason="warming up")
        prior = candles[-(self.period + 1):-1]            # prior `period` bars, excluding current
        hi = max(c.high for c in prior)
        lo = min(c.low for c in prior)
        avg_vol = sum(c.volume for c in prior) / self.period
        cur = candles[-1]
        price, vol = cur.close, cur.volume
        vol_ok = avg_vol > 0 and vol >= self.vol_mult * avg_vol
        ind = {"upper": round(hi, 2), "lower": round(lo, 2), "price": round(price, 2),
               "vol": round(vol, 2), "avg_vol": round(avg_vol, 2), "vol_mult": self.vol_mult}
        # Symmetric, volume-confirmed both ways: LONG a confirmed break above the high, SHORT a
        # confirmed break below the low. The engine closes-and-flips an opposing position.
        if price > hi and vol_ok:
            return Signal("LONG", confidence=0.6,
                          reason=f"breakout {price:.0f}>{hi:.0f} on {vol:.0f} vol "
                                 f"(>= {self.vol_mult}x avg {avg_vol:.0f})", indicators=ind)
        if price < lo and vol_ok:
            return Signal("SHORT", confidence=0.6,
                          reason=f"breakdown {price:.0f}<{lo:.0f} on {vol:.0f} vol "
                                 f"(>= {self.vol_mult}x avg {avg_vol:.0f})", indicators=ind)
        if (price > hi or price < lo) and not vol_ok:
            return Signal("HOLD", reason=f"breakout unconfirmed: vol {vol:.0f} < "
                          f"{self.vol_mult}x avg {avg_vol:.0f}", indicators=ind)
        return Signal("HOLD", reason="inside channel", indicators=ind)
