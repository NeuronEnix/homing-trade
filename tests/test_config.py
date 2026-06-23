"""from_env: the single env->Config layer. These lock in that built features have an operator
switch — several portfolio gates / autonomous loops (regime filter, allocator, committee threshold,
comms inbound, continuous backtest) were reachable only by editing code until they were wired here."""
import dataclasses
from homing_trade.config import Config, from_env


def _env(monkeypatch, **kw):
    # Isolate from the real .env (point dotenv at a nonexistent file) and the ambient environment.
    for k in list(__import__("os").environ):
        if k.startswith(("HT_", "AI_", "REGIME_", "ALLOCATOR", "COMMITTEE", "COMMS_",
                         "CONTINUOUS_", "REFLECTION_", "RESEARCH_", "TRUST_", "FNG_", "DERIVS_",
                         "PRICE_REF_", "NEWS_", "COINDCX_SIGNAL_")):
            monkeypatch.delenv(k, raising=False)
    for k, v in kw.items():
        monkeypatch.setenv(k, v)
    return from_env(Config(), dotenv_path="/nonexistent/.env")


def test_newly_wired_switches_default_off(monkeypatch):
    cfg = _env(monkeypatch)
    assert cfg.regime_filter_enabled is False
    assert cfg.allocator_enabled is False
    assert cfg.comms_inbound_enabled is False
    assert cfg.continuous_backtest_enabled is False


def test_regime_filter_switch_and_weights(monkeypatch):
    cfg = _env(monkeypatch, REGIME_FILTER_IS_ENABLED="true",
               REGIME_UNFAVORED_WEIGHT="0.3", REGIME_COMMITTEE_THRESHOLD_SCALE="2.0")
    assert cfg.regime_filter_enabled is True
    assert cfg.regime_unfavored_weight == 0.3
    assert cfg.regime_committee_threshold_scale == 2.0


def test_allocator_committee_backtest_comms_switches(monkeypatch):
    cfg = _env(monkeypatch, ALLOCATOR_IS_ENABLED="1", ALLOCATOR_LOOKBACK="40",
               COMMITTEE_THRESHOLD="0.5", COMMS_INBOUND_IS_ENABLED="yes", COMMS_POLL_IN_SEC="15",
               CONTINUOUS_BACKTEST_IS_ENABLED="on", CONTINUOUS_BACKTEST_POLL_IN_SEC="3600")
    assert cfg.allocator_enabled is True and cfg.allocator_lookback == 40
    assert cfg.committee_threshold == 0.5
    assert cfg.comms_inbound_enabled is True and cfg.comms_poll_sec == 15
    assert cfg.continuous_backtest_enabled is True and cfg.continuous_backtest_poll_sec == 3600


def test_trust_cutoff_override(monkeypatch):
    assert _env(monkeypatch, TRUST_CUTOFF_ISO="2025-03-01").trust_cutoff_iso == "2025-03-01"
    assert _env(monkeypatch).trust_cutoff_iso == "2026-01-01"   # default preserved


def test_falsey_env_values_turn_switches_off(monkeypatch):
    # a bare Config has these on for the test, so prove "false"/"0" actually flip them off
    base = dataclasses.replace(Config(), regime_filter_enabled=True, allocator_enabled=True)
    import os
    for k in ("REGIME_FILTER_IS_ENABLED", "ALLOCATOR_IS_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REGIME_FILTER_IS_ENABLED", "false")
    monkeypatch.setenv("ALLOCATOR_IS_ENABLED", "0")
    cfg = from_env(base, dotenv_path="/nonexistent/.env")
    assert cfg.regime_filter_enabled is False and cfg.allocator_enabled is False
