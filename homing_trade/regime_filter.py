"""Regime-aware portfolio gate (Phase 7 #3).

`classify_regime()` (indicators.py) labels each tick trend_up / trend_down / chop / transition /
unknown. This module turns that label into the highest-leverage portfolio control:

  * regime_weight(name, regime): a per-strategy MULTIPLIER on the allocator's position sizing —
    trend-following strategies are favored in trends and de-weighted in chop; mean-reverters the
    reverse;
  * committee_threshold_scale(regime): a multiplier on the committee's consensus threshold — demand
    STRONGER consensus to act when the market is NOT trending (chop / transition / unknown), where
    consensus entries whipsaw.

Both are conservative: a NEUTRAL-style strategy, or an ambiguous regime, never gets penalized
(multiplier 1.0). The gate only ever REDUCES exposure / RAISES the bar where a strategy's edge is
structurally weak — it never invents conviction. Default-OFF (cfg.regime_filter_enabled): when off,
the engine never calls these and behavior is unchanged.
"""
TREND = "trend"
REVERT = "revert"
NEUTRAL = "neutral"

# Static style of each registered mechanical strategy. Adaptive/meta strategies (rl_qlearn,
# committee) and the AI traders are intentionally absent -> NEUTRAL (no static regime bias; the
# committee gets its own threshold scale instead).
STRATEGY_STYLE = {
    "ma_trend": TREND, "macd": TREND, "donchian": TREND, "supertrend": TREND,
    "vol_breakout": TREND, "ttm_squeeze": TREND,
    "rsi_revert": REVERT, "bollinger": REVERT, "grid": REVERT, "zscore_revert": REVERT,
}

_TRENDING = {"trend_up", "trend_down"}
_AMBIGUOUS = {None, "unknown", "transition"}


def strategy_style(name):
    return STRATEGY_STYLE.get(name, NEUTRAL)


def regime_weight(name, regime, *, favored=1.0, unfavored=0.5):
    """Allocator weight multiplier for `name` under `regime`. Returns `favored` (1.0) unless the
    strategy's style is structurally mismatched to a CLEAR regime, in which case `unfavored` (<1).
    NEUTRAL style or an ambiguous regime (unknown/transition/None) is never penalized."""
    style = strategy_style(name)
    if style == NEUTRAL or regime in _AMBIGUOUS:
        return favored
    if style == TREND:
        return favored if regime in _TRENDING else unfavored      # trend edge dies in chop
    return favored if regime == "chop" else unfavored             # REVERT edge dies in trends


def committee_threshold_scale(regime, *, trending=1.0, non_trending=1.5):
    """Multiplier on the committee's consensus threshold: `trending` in a clear trend (act on
    conviction), `non_trending` otherwise (demand a higher bar where consensus whipsaws)."""
    return trending if regime in _TRENDING else non_trending


def entry_allowed(name, regime, side):
    """HARD entry gate (stronger than regime_weight's soft sizing tilt): may strategy `name` OPEN a
    `side` ('LONG'/'SHORT') position under `regime`?

      * TREND style  -> only WITH a confirmed, direction-aligned trend: LONG in trend_up, SHORT in
        trend_down. Blocks the chop/transition/unknown whipsaw that is the trend-followers' whole
        loss (and blocks counter-trend entries outright).
      * REVERT style -> only in 'chop', where mean-reversion has an edge; a reverter that fades an
        extreme inside a strong trend just gets run over.
      * NEUTRAL style (AI traders, committee, anything unmapped) -> never gated; they own their own
        directional logic.

    Returns True when the open is permitted. Pure + side-effect free, so it unit-tests in isolation
    and the engine can call it on both fresh entries and reversal-flips."""
    style = strategy_style(name)
    if style == NEUTRAL:
        return True
    if style == TREND:
        return (regime == "trend_up" and side == "LONG") or (regime == "trend_down" and side == "SHORT")
    return regime == "chop"   # REVERT: range-bound only, either side
