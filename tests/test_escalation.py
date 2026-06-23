"""Phase 11 #2: the escalation policy. Pure + deterministic, so every trigger and the fail-safe are
unit-testable with no network/clock."""
from homing_trade.escalation import (escalation_for, Thresholds, Verdict,
                                      ROUTINE, NOTABLE, ESCALATION, max_level)


def _entry(**kw):
    base = dict(kind="entry", strategy="ma_trend", symbol="B-BTC_USDT", side="LONG",
                notional=100.0, confidence=0.6, decision_id="d1")
    base.update(kw)
    return base


# --- fail-safe (the crux) -----------------------------------------------------------------------
def test_no_action_escalates():
    assert escalation_for(None).level == ESCALATION
    assert escalation_for({}).level == ESCALATION


def test_missing_core_fact_escalates():
    assert escalation_for({"kind": "entry"}).level == ESCALATION         # no strategy
    assert escalation_for({"strategy": "x"}).level == ESCALATION         # no kind


def test_size_trigger_active_but_input_missing_escalates():
    # size_pct_of_equity is on by default; if equity is unknown the policy can't tell -> escalate.
    v = escalation_for(_entry(notional=10.0), ctx={}, th=Thresholds())
    assert v.level == ESCALATION and "size:missing-input" in v.reasons


# --- ordinary path ------------------------------------------------------------------------------
def test_routine_in_envelope_entry():
    ctx = dict(equity=5000.0, known_combos={("ma_trend", "B-BTC_USDT", "LONG")})
    v = escalation_for(_entry(notional=100.0), ctx, Thresholds(novelty_k=None))
    assert v.level == ROUTINE and v.reasons == ()


# --- size ---------------------------------------------------------------------------------------
def test_size_pct_of_equity_escalates():
    ctx = dict(equity=1000.0, known_combos={("ma_trend", "B-BTC_USDT", "LONG")})
    v = escalation_for(_entry(notional=300.0), ctx, Thresholds(size_pct_of_equity=0.25, novelty_k=None))
    assert v.level == ESCALATION and any("size:>=25%" in r for r in v.reasons)


def test_size_abs_cap_escalates():
    ctx = dict(equity=1e9, known_combos={("ma_trend", "B-BTC_USDT", "LONG")})
    v = escalation_for(_entry(notional=5000.0), ctx,
                       Thresholds(size_pct_of_equity=None, size_abs_cap=4000.0, novelty_k=None))
    assert v.level == ESCALATION and "size:>=abs-cap" in v.reasons


# --- novelty ------------------------------------------------------------------------------------
def test_novel_combo_escalates():
    ctx = dict(equity=5000.0, known_combos={("ma_trend", "B-BTC_USDT", "LONG")})
    v = escalation_for(_entry(side="SHORT", notional=100.0), ctx, Thresholds(novelty_k=None))
    assert v.level == ESCALATION and "novelty:new-strategy/symbol/side" in v.reasons


def test_size_far_above_baseline_escalates():
    ctx = dict(equity=5000.0, known_combos={("ma_trend", "B-BTC_USDT", "LONG")},
               baseline_mean=100.0, baseline_std=10.0)
    v = escalation_for(_entry(notional=200.0), ctx, Thresholds(novelty_k=2.0))
    assert v.level == ESCALATION and any("baseline" in r for r in v.reasons)
    # within the band -> not a novelty escalation
    v2 = escalation_for(_entry(notional=115.0), ctx, Thresholds(novelty_k=2.0))
    assert v2.level == ROUTINE


# --- posture change always escalates ------------------------------------------------------------
def test_posture_change_always_escalates():
    v = escalation_for({"kind": "posture_change", "strategy": "ma_trend"}, {}, Thresholds())
    assert v.level == ESCALATION and "risk-posture-change" in v.reasons


# --- drawdown / vol / loss streak ---------------------------------------------------------------
def test_drawdown_near_kill_switch_escalates():
    ctx = dict(equity=5000.0, day_loss=4000.0, max_daily_loss=5000.0,
               known_combos={("ma_trend", "B-BTC_USDT", "LONG")})
    v = escalation_for(_entry(notional=100.0), ctx, Thresholds(drawdown_frac=0.7, novelty_k=None))
    assert v.level == ESCALATION and any("drawdown" in r for r in v.reasons)


def test_vol_spike_escalates():
    ctx = dict(equity=5000.0, realized_vol=0.08, vol_threshold=0.04,
               known_combos={("ma_trend", "B-BTC_USDT", "LONG")})
    v = escalation_for(_entry(notional=100.0), ctx, Thresholds(vol_spike_mult=1.5, novelty_k=None))
    assert v.level == ESCALATION and "vol-spike" in v.reasons


def test_loss_streak_escalates():
    ctx = dict(equity=5000.0, loss_streak=3, known_combos={("ma_trend", "B-BTC_USDT", "LONG")})
    v = escalation_for(_entry(notional=100.0), ctx, Thresholds(loss_streak=3, novelty_k=None))
    assert v.level == ESCALATION and any("loss-streak" in r for r in v.reasons)


# --- notable ------------------------------------------------------------------------------------
def test_stop_exit_is_notable():
    v = escalation_for({"kind": "exit", "strategy": "ma_trend", "exit_reason": "stop"}, {}, Thresholds())
    assert v.level == NOTABLE and "exit:stop" in v.reasons


def test_regime_flip_and_first_trade_notable():
    ctx = dict(equity=5000.0, regime_flip=True, first_trade=True,
               known_combos={("ma_trend", "B-BTC_USDT", "LONG")})
    v = escalation_for(_entry(notional=100.0), ctx, Thresholds(novelty_k=None))
    assert v.level == NOTABLE
    assert "regime-flip" in v.reasons and "first-trade-of-session" in v.reasons


def test_escalation_beats_notable():
    # a stop exit (notable) on a posture-change shape still escalates; severity is the max.
    ctx = dict(equity=1000.0, regime_flip=True, known_combos=set())
    v = escalation_for(_entry(notional=900.0), ctx, Thresholds(size_pct_of_equity=0.25, novelty_k=None))
    assert v.level == ESCALATION and "regime-flip" in v.reasons   # notable reason carried along


def test_max_level_helper():
    assert max_level(ROUTINE, NOTABLE) == NOTABLE
    assert max_level(ESCALATION, NOTABLE) == ESCALATION
    assert max_level(ROUTINE, ROUTINE) == ROUTINE


def test_bad_numeric_facts_read_as_missing():
    # NaN/inf/garbage notional with size rule active -> treated as missing -> escalate (fail-safe)
    ctx = dict(equity=5000.0, known_combos={("ma_trend", "B-BTC_USDT", "LONG")})
    v = escalation_for(_entry(notional=float("inf")), ctx, Thresholds())
    assert v.level == ESCALATION and "size:missing-input" in v.reasons
