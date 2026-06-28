"""Supertrend — an ATR-banded trend-following candidate strategy (Phase 7 #2).

Supertrend places bands at hl2 ± mult·ATR with the standard 'final band' carry-forward rule: the
upper band can only ratchet down (and the lower band up) while the trend holds, and price closing
beyond a band flips the trend. We trade the FLIP symmetrically: LONG on a down→up flip, SHORT on the
up→down flip (the engine closes-and-flips an opposing position). Entry-on-flip-only, so it never
re-enters mid-trend. The seed bar's trend
is arbitrary (derived from one bar with no prior context), so we require ≥3 computed bars before
acting — the flip reference is then a recursion-confirmed trend, not the raw seed.

It is a CANDIDATE: registered in the skill factory (so the backtest + walk-forward harness can
evaluate it by name) but NOT added to the default enabled_skills — it joins the live paper tournament
only once it earns promotion out-of-sample (Phase 7 promotion discipline).
"""
from homing_trade.skills.base import Strategy
from homing_trade.models import Signal
from homing_trade.skills.indicators import true_ranges


def supertrend_series(candles, period=10, mult=3.0):
    """List of (trend, line) aligned to candles[period:], or [] if too short. trend ∈ {up, down};
    line is the active band (lower band in an uptrend, upper band in a downtrend). Wilder ATR +
    the canonical final-band recursion."""
    n = len(candles)
    if n < period + 1:
        return []
    trs = true_ranges(candles)                       # trs[i-1] is the TR at candle i
    atr_v = sum(trs[:period]) / period               # Wilder ATR seeded at candle index `period`
    atrs = {period: atr_v}
    for idx in range(period + 1, n):
        atr_v = (atr_v * (period - 1) + trs[idx - 1]) / period
        atrs[idx] = atr_v
    out, final_upper, final_lower, trend = [], None, None, "up"
    for i in range(period, n):
        hl2 = (candles[i].high + candles[i].low) / 2
        basic_upper = hl2 + mult * atrs[i]
        basic_lower = hl2 - mult * atrs[i]
        if final_upper is None:                      # first computed bar: seed bands + trend
            final_upper, final_lower = basic_upper, basic_lower
            trend = "up" if candles[i].close >= hl2 else "down"
        else:
            prev_close = candles[i - 1].close
            final_upper = basic_upper if (basic_upper < final_upper or prev_close > final_upper) else final_upper
            final_lower = basic_lower if (basic_lower > final_lower or prev_close < final_lower) else final_lower
            if candles[i].close > final_upper:
                trend = "up"
            elif candles[i].close < final_lower:
                trend = "down"                       # else: trend persists
        line = final_lower if trend == "up" else final_upper
        out.append((trend, round(line, 2)))
    return out


class Supertrend(Strategy):
    name = "supertrend"

    def __init__(self, period=10, mult=3.0):
        self.period = period
        self.mult = mult

    def on_candle(self, candles, position):
        series = supertrend_series(candles, self.period, self.mult)
        if len(series) < 3:                          # need a recursion-confirmed reference, not the seed
            return Signal("HOLD", reason="warming up")
        prev_trend = series[-2][0]
        trend, line = series[-1]
        ind = {"supertrend": line, "trend": trend}
        # Symmetric: trade the flip in either direction; the engine closes-and-flips an opposing
        # position, so an up->down flip reverses a long into a short rather than just closing it.
        if trend == "up" and prev_trend == "down":
            return Signal("LONG", confidence=0.6, reason=f"supertrend flip up @ {line:.0f}", indicators=ind)
        if trend == "down" and prev_trend == "up":
            return Signal("SHORT", confidence=0.6, reason=f"supertrend flip down @ {line:.0f}", indicators=ind)
        return Signal("HOLD", reason=f"trend {trend}", indicators=ind)
