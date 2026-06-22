"""The proposals approval-gate: nothing self-applies, and protected zones can't be proposed."""
import json
import pytest
from homing_trade.db import Database


def make(tmp_path):
    db = Database(str(tmp_path / "prop.db"))
    db.ensure_strategy("ma", 5000.0)
    return db


def test_table_exists_and_classified(tmp_path):
    assert "proposals" in make(tmp_path).table_names()


def test_create_and_read_pending(tmp_path):
    db = make(tmp_path)
    pid = db.create_proposal("ma", "param", {"ema_period": 34}, "trend filter too fast", 1000,
                             source_reflection_id=7)
    p = db.get_proposal(pid)
    assert p["status"] == "pending" and p["kind"] == "param"
    assert json.loads(p["payload_json"]) == {"ema_period": 34}
    assert p["rationale"] == "trend filter too fast" and p["source_reflection_id"] == 7
    assert [x["id"] for x in db.pending_proposals("ma")] == [pid]


def test_decide_flow_and_idempotent(tmp_path):
    db = make(tmp_path)
    pid = db.create_proposal("ma", "playbook", {"rules": ["skip chop"]}, "why", 1000)
    assert db.decide_proposal(pid, "approved", "human:web", 2000) is True
    p = db.get_proposal(pid)
    assert p["status"] == "approved" and p["decided_by"] == "human:web" and p["decided_ts"] == 2000
    assert db.pending_proposals() == []                       # no longer pending
    # re-deciding an already-decided proposal is a no-op (can't flip approved->rejected)
    assert db.decide_proposal(pid, "rejected", "human:web", 3000) is False
    assert db.get_proposal(pid)["status"] == "approved"


def test_unknown_kind_rejected(tmp_path):
    db = make(tmp_path)
    with pytest.raises(ValueError):
        db.create_proposal("ma", "delete_everything", {}, "nope", 1000)


def test_invalid_decision_rejected(tmp_path):
    db = make(tmp_path)
    pid = db.create_proposal("ma", "param", {"ema_period": 21}, "x", 1000)
    with pytest.raises(ValueError):
        db.decide_proposal(pid, "maybe", "human", 2000)


@pytest.mark.parametrize("payload", [
    {"max_daily_loss": 0.0},                 # kill-switch / hard risk limit
    {"leverage_max": 100},                   # leverage ceiling
    {"leverage_min": 100},                   # bypasses leverage_max via effective_leverage!
    {"leverage": 50},                         # base leverage
    {"risk_pct": 0.99},                       # per-trade risk sizing
    {"stop_pct": 0.5},                        # stop distance
    {"fee": 0.0}, {"slippage": 0.0},          # execution fidelity
    {"live_dry_run": False},                 # live-arming
    {"trading_enabled": True},               # master switch
    {"committee_threshold": 0.0},             # signal gate
    {"anthropic_api_key": "sk-..."},         # secret (substring match)
    {"coindcx_key_env": "X"},                 # secret-env name (was an asymmetric gap)
    {"nested": {"webhook_url": "http://x"}}, # protected at depth
    {"opts": [{"max_daily_loss": 1}]},        # protected inside a list
    {"field": "leverage_min", "value": 9999},# FIELD-AS-VALUE: protected name in a value
])
def test_protected_fields_can_never_be_proposed(tmp_path, payload):
    db = make(tmp_path)
    with pytest.raises(ValueError):
        db.create_proposal("ma", "param", payload, "should be blocked", 1000)
    assert db.recent_proposals() == []        # nothing was written


def test_protected_denylist_covers_real_config_risk_fields(tmp_path):
    # Fail-closed coverage: every safety-relevant Config field must be un-proposable. Seeded
    # from the ACTUAL config so the gate can't silently regress when config grows.
    from homing_trade.config import Config
    cfg = Config()
    SAFETY_FIELDS = [f for f in vars(cfg) if any(t in f.lower() for t in (
        "leverage", "risk", "fee", "slippage", "daily", "per_day", "stop", "trading_enabled",
        "live", "dry_run", "webhook", "_key_env", "secret", "token", "chat_id", "committee"))]
    assert SAFETY_FIELDS                      # sanity: the heuristic found some
    db = make(tmp_path)
    for f in SAFETY_FIELDS:
        with pytest.raises(ValueError, match="protected"):
            db.create_proposal("ma", "param", {f: 1}, "blocked", 1000)


def test_legit_param_proposal_allowed(tmp_path):
    db = make(tmp_path)
    pid = db.create_proposal("ma", "param", {"ema_period": 21, "rsi_threshold": 70,
                                             "allocator_lookback": 30, "rl_alpha": 0.2},
                             "tune entry filter", 1000)
    assert db.get_proposal(pid)["status"] == "pending"   # ordinary tunables are fine
