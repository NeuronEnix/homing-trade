"""Phase 10 #1: the explicit live-trading arming gate.

Covers mode resolution, the fail-closed LIVE preconditions, and that the broker selection defaults
to PAPER and FAILS SAFE for any live mode (live execution isn't integrated yet). Building this arms
nothing; the default config must always resolve to paper."""
import pytest

from homing_trade import arming
from homing_trade.broker import Broker
from homing_trade.config import Config


# --- mode resolution (pure) ------------------------------------------------------------------
@pytest.mark.parametrize("enabled,dry,keys,expected", [
    (False, True, True, arming.PAPER),       # default-ish: disabled -> paper
    (False, False, True, arming.PAPER),      # disabled overrides everything
    (True, True, True, arming.LIVE_DRY_RUN), # enabled but dry-run -> simulate
    (True, False, False, arming.LIVE_DRY_RUN),  # enabled, real wanted, but NO keys -> can't be live
    (True, False, True, arming.LIVE),        # the only path to real orders
])
def test_resolve_mode(enabled, dry, keys, expected):
    assert arming.resolve_mode(live_enabled=enabled, live_dry_run=dry, keys_present=keys) == expected


def test_config_default_resolves_to_paper():
    c = Config()
    assert arming.resolve_mode(live_enabled=c.live_enabled, live_dry_run=c.live_dry_run,
                               keys_present=True) == arming.PAPER


# --- fail-closed LIVE preconditions ----------------------------------------------------------
def _live_cfg(**over):
    # a config that WOULD resolve to LIVE; tests knock out one precondition at a time
    base = dict(live_enabled=True, live_dry_run=False, trading_enabled=True,
                max_daily_loss=50.0, live_capital_cap=100.0)
    base.update(over)
    return Config(**base)


def test_paper_and_dry_run_never_need_preconditions():
    assert arming.assert_safe_to_arm(Config(), keys_present=False) == arming.PAPER
    assert arming.assert_safe_to_arm(Config(live_enabled=True), keys_present=False) == arming.LIVE_DRY_RUN


def test_live_passes_when_all_preconditions_hold():
    assert arming.assert_safe_to_arm(_live_cfg(), keys_present=True) == arming.LIVE


@pytest.mark.parametrize("over,needle", [
    ({"trading_enabled": False}, "master switch"),
    ({"max_daily_loss": 0}, "max_daily_loss"),
    ({"live_capital_cap": 0}, "live_capital_cap"),
])
def test_live_refused_when_a_precondition_missing(over, needle):
    # keys ARE present here (so the mode resolves to LIVE), but a precondition is knocked out
    with pytest.raises(PermissionError) as exc:
        arming.assert_safe_to_arm(_live_cfg(**over), keys_present=True)
    assert needle in str(exc.value)


def test_missing_keys_degrades_to_dry_run_never_raises():
    # asking for real orders (live_dry_run=False) without keys must FALL BACK to simulation, not
    # raise and not go live — the core fail-safe.
    assert arming.assert_safe_to_arm(_live_cfg(), keys_present=False) == arming.LIVE_DRY_RUN


def test_arming_problems_explains_what_blocks_live():
    # the UI/logs explainer surfaces every blocker (even ones the assert can't reach because the
    # mode degrades first, e.g. missing keys)
    probs = arming.arming_problems(Config(live_enabled=True, live_dry_run=False), keys_present=False)
    assert any("API keys" in p for p in probs)
    assert any("max_daily_loss" in p for p in probs) and any("live_capital_cap" in p for p in probs)
    assert arming.arming_problems(_live_cfg(), keys_present=True) == []   # fully armed -> no blockers


# --- broker selection ------------------------------------------------------------------------
def test_select_broker_default_is_paper(monkeypatch):
    monkeypatch.setattr(arming, "keys_present_in_env", lambda cfg, **k: True)
    broker, mode = arming.select_broker(Config())
    assert mode == arming.PAPER and isinstance(broker, Broker)


def test_select_broker_fails_safe_for_live(monkeypatch):
    # all LIVE preconditions met, but live EXECUTION isn't integrated -> refuse, never half-trade
    monkeypatch.setattr(arming, "keys_present_in_env", lambda cfg, **k: True)
    with pytest.raises(NotImplementedError) as exc:
        arming.select_broker(_live_cfg())
    assert "live execution is not integrated" in str(exc.value)


def test_select_broker_fails_safe_for_live_dry_run(monkeypatch):
    # even enabled+dry-run can't run through the engine yet -> fail safe (paper only for now)
    monkeypatch.setattr(arming, "keys_present_in_env", lambda cfg, **k: False)
    with pytest.raises(NotImplementedError):
        arming.select_broker(Config(live_enabled=True))


def test_keys_present_reads_both_key_and_secret(monkeypatch):
    monkeypatch.setattr("homing_trade.dotenv.coindcx_keys", lambda cfg, dotenv_path=".env": ("k", "s"))
    assert arming.keys_present_in_env(Config()) is True
    monkeypatch.setattr("homing_trade.dotenv.coindcx_keys", lambda cfg, dotenv_path=".env": ("k", ""))
    assert arming.keys_present_in_env(Config()) is False
