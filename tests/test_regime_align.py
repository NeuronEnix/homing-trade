"""Hard regime-alignment entry gate: trend strategies may only OPEN with a confirmed aligned
trend (LONG in trend_up, SHORT in trend_down); mean-reverters only in chop; neutral styles are
never gated. This is what makes symmetric shorting profitable instead of doubling chop whipsaw."""
from homing_trade.regime_filter import entry_allowed


# --- trend-followers: only WITH an aligned, confirmed trend ---
def test_trend_long_allowed_only_in_trend_up():
    assert entry_allowed("macd", "trend_up", "LONG") is True
    assert entry_allowed("macd", "trend_down", "LONG") is False
    assert entry_allowed("macd", "chop", "LONG") is False
    assert entry_allowed("macd", "transition", "LONG") is False
    assert entry_allowed("macd", "unknown", "LONG") is False


def test_trend_short_allowed_only_in_trend_down():
    assert entry_allowed("donchian", "trend_down", "SHORT") is True
    assert entry_allowed("donchian", "trend_up", "SHORT") is False
    assert entry_allowed("donchian", "chop", "SHORT") is False


def test_ma_trend_is_treated_as_trend_style():
    assert entry_allowed("ma_trend", "trend_down", "SHORT") is True
    assert entry_allowed("ma_trend", "chop", "SHORT") is False


# --- mean-reverters: only in chop, either side ---
def test_revert_allowed_only_in_chop():
    assert entry_allowed("rsi_revert", "chop", "LONG") is True
    assert entry_allowed("rsi_revert", "chop", "SHORT") is True
    assert entry_allowed("bollinger", "trend_up", "LONG") is False
    assert entry_allowed("grid", "trend_down", "SHORT") is False


# --- neutral styles (AI traders / committee) are never gated ---
def test_neutral_never_gated():
    assert entry_allowed("llm_claude_code", "chop", "LONG") is True
    assert entry_allowed("committee", "unknown", "SHORT") is True
