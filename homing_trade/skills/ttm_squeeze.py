"""TTM Squeeze — a volatility-compression breakout candidate (Phase 7 #2).

The squeeze is ON when the Bollinger Bands sit INSIDE the Keltner Channels (volatility compressed);
it RELEASES when the bands expand back outside the channel. We trade the release in the direction of
momentum: go LONG when a squeeze releases with positive momentum (close above the BB midline). Close a
long when the squeeze re-engages or momentum turns negative. Long-only.

  * Bollinger: SMA(close, period) ± bb_std·stdev
  * Keltner:   EMA(close, period) ± kc_mult·ATR(period)
  * squeeze_on = BB upper < KC upper AND BB lower > KC lower
  * momentum   = close − BB midline   (simple, mechanical proxy)

CANDIDATE: registered in the skill factory (evaluable by the backtest + walk-forward harness) but NOT
in the default enabled_skills — it joins the live paper tournament only on out-of-sample promotion.
"""
from homing_trade.skills.base import Strategy
from homing_trade.models import Signal
from homing_trade.skills.indicators import bollinger, ema, atr


def squeeze_state(candles, period=20, bb_std=2.0, kc_mult=1.5):
    """(squeeze_on: bool, momentum: float) for the latest bar, or None if too short."""
    if len(candles) < period + 1:                         # ATR needs period+1 candles
        return None
    closes = [c.close for c in candles]
    bmid, bup, blo = bollinger(closes, period, bb_std)
    kmid = ema(closes, period)
    a = atr(candles, period)
    if bup is None or kmid is None or a is None:
        return None
    kup, klo = kmid + kc_mult * a, kmid - kc_mult * a
    on = (bup < kup) and (blo > klo)                      # BB inside KC -> volatility compressed
    return on, closes[-1] - bmid


class TtmSqueeze(Strategy):
    name = "ttm_squeeze"

    def __init__(self, period=20, bb_std=2.0, kc_mult=1.5):
        self.period = period
        self.bb_std = bb_std
        self.kc_mult = kc_mult

    def on_candle(self, candles, position):
        cur = squeeze_state(candles, self.period, self.bb_std, self.kc_mult)
        prev = squeeze_state(candles[:-1], self.period, self.bb_std, self.kc_mult)
        if cur is None or prev is None:
            return Signal("HOLD", reason="warming up")
        on, mom = cur
        prev_on, _ = prev
        released = prev_on and not on                     # compression just broke
        ind = {"squeeze": on, "momentum": round(mom, 2)}
        is_long = position is not None and position.side == "LONG"
        if position is None and released and mom > 0:
            return Signal("LONG", confidence=0.6, reason=f"squeeze release up (mom {mom:.1f})",
                          indicators=ind)
        if is_long and (on or mom < 0):
            return Signal("CLOSE", confidence=0.6,
                          reason="squeeze re-engaged" if on else f"momentum turned down ({mom:.1f})",
                          indicators=ind)
        return Signal("HOLD", reason=f"squeeze {'on' if on else 'off'}", indicators=ind)
