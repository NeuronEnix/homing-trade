"""Phase-4 #9: per-playbook-version performance + auto rollback proposal + disconfirmation guard.

Trades are attributed to the playbook version active at entry (decision_log.playbook_version,
tagged in #6). When the CURRENT active version performs materially worse than the parent it
superseded, a human-gated proposal to roll back to the parent's rules is filed (PROPOSE-only,
idempotent). The disconfirmation guard flags regimes the current version stopped trading that its
parent did (beliefs the bot may no longer be testing).
"""
import json
from homing_trade.repository import Repository
from homing_trade.selfquery import SelfQuery
from homing_trade.playbook_rollback import propose_rollback


def seed_versioned(repo, strategy, version, n, win, base_ts, pid0, regime="trend_up"):
    for i in range(n):
        pid, ts, did = pid0 + i, base_ts + i * 10, f"d{pid0 + i}"
        repo.log_decision(strategy, ts, ts, "LONG", 0.6, "r", {}, decision_id=did,
                          intended_action="LONG", taken_action="LONG", regime=regime,
                          playbook_version=version)
        repo.record_trade(strategy, pid, "LONG", "OPEN", 100.0, 1, 0.1, -0.1, ts,
                          decision_id=did, regime_at_entry=regime)
        exit_px = 110.0 if win else 95.0
        repo.record_trade(strategy, pid, "LONG", "CLOSE", exit_px, 1, 0.1, exit_px - 100.0,
                          ts + 5, exit_reason="signal")


def seed_degraded(repo, strategy="ma", n=4):
    """Parent v1 (winners) superseded by current v2 (losers) — a degrading playbook."""
    repo.ensure_strategy(strategy, 5000.0)
    repo.publish_playbook("v1", strategy, 1000, {"rules": ["trend only"]})
    repo.publish_playbook("v2", strategy, 2000, {"rules": ["trend only", "also fade spikes"]},
                          parent_version="v1")
    repo.retire_playbook("v1", 2000)                       # v2 is the active version
    seed_versioned(repo, strategy, "v1", n, win=True, base_ts=1000, pid0=1)
    seed_versioned(repo, strategy, "v2", n, win=False, base_ts=5000, pid0=101)
    repo.rebuild_trade_outcomes()


def test_playbook_performance_attributes_by_version(tmp_path):
    repo = Repository.open(str(tmp_path / "p.db"))
    seed_degraded(repo, n=4)
    perf = SelfQuery(repo).playbook_performance("ma", as_of=10_000)
    assert perf["v1"]["trades"] == 4 and perf["v1"]["win_rate"] == 1.0
    assert perf["v2"]["trades"] == 4 and perf["v2"]["win_rate"] == 0.0
    assert perf["v2"]["avg_pnl"] < perf["v1"]["avg_pnl"]
    repo.close()


def test_propose_rollback_when_current_degrades(tmp_path):
    repo = Repository.open(str(tmp_path / "p.db"))
    seed_degraded(repo, n=4)
    pid = propose_rollback(repo, "ma", 10_000, min_trades=3)
    assert pid is not None
    p = repo.get_proposal(pid)
    assert p["kind"] == "playbook" and p["status"] == "pending"
    payload = json.loads(p["payload_json"])
    assert payload["rollback_to"] == "v1"
    assert payload["rules"] == ["trend only"]              # the parent's rules
    assert payload["parent_version"] == "v2"               # supersedes the degrading current one
    repo.close()


def test_no_rollback_when_current_not_worse(tmp_path):
    repo = Repository.open(str(tmp_path / "p.db"))
    repo.ensure_strategy("ma", 5000.0)
    repo.publish_playbook("v1", "ma", 1000, {"rules": ["a"]})
    repo.publish_playbook("v2", "ma", 2000, {"rules": ["a", "b"]}, parent_version="v1")
    repo.retire_playbook("v1", 2000)
    seed_versioned(repo, "ma", "v1", 4, win=False, base_ts=1000, pid0=1)   # parent: losers
    seed_versioned(repo, "ma", "v2", 4, win=True, base_ts=5000, pid0=101)  # current: winners
    repo.rebuild_trade_outcomes()
    assert propose_rollback(repo, "ma", 10_000, min_trades=3) is None      # current is BETTER
    repo.close()


def test_no_rollback_without_enough_trades(tmp_path):
    repo = Repository.open(str(tmp_path / "p.db"))
    seed_degraded(repo, n=2)                                # only 2 trades/version
    assert propose_rollback(repo, "ma", 10_000, min_trades=5) is None
    repo.close()


def test_no_rollback_without_a_parent(tmp_path):
    repo = Repository.open(str(tmp_path / "p.db"))
    repo.ensure_strategy("ma", 5000.0)
    repo.publish_playbook("v1", "ma", 1000, {"rules": ["a"]})   # no parent
    seed_versioned(repo, "ma", "v1", 4, win=False, base_ts=1000, pid0=1)
    repo.rebuild_trade_outcomes()
    assert propose_rollback(repo, "ma", 10_000, min_trades=3) is None
    repo.close()


def test_rollback_is_idempotent(tmp_path):
    repo = Repository.open(str(tmp_path / "p.db"))
    seed_degraded(repo, n=4)
    assert propose_rollback(repo, "ma", 10_000, min_trades=3) is not None
    assert propose_rollback(repo, "ma", 20_000, min_trades=3) is None      # already pending
    assert len([p for p in repo.pending_proposals("ma") if p["kind"] == "playbook"]) == 1
    repo.close()


def seed_mixed(repo, strategy, version, pnls, base_ts, pid0, regime="trend_up"):
    """Trades with explicit per-trade net PnL (win iff pnl>0) under `version`."""
    for i, p in enumerate(pnls):
        pid, ts, did = pid0 + i, base_ts + i * 10, f"d{pid0 + i}"
        repo.log_decision(strategy, ts, ts, "LONG", 0.6, "r", {}, decision_id=did,
                          intended_action="LONG", taken_action="LONG", regime=regime,
                          playbook_version=version)
        repo.record_trade(strategy, pid, "LONG", "OPEN", 100.0, 1, 0.0, 0.0, ts,
                          decision_id=did, regime_at_entry=regime)
        repo.record_trade(strategy, pid, "LONG", "CLOSE", 100.0 + p, 1, 0.0, p, ts + 5,
                          exit_reason="signal")


def test_no_rollback_of_a_rollback_prevents_oscillation(tmp_path):
    # The current active version is ITSELF a rollback; even though it underperforms its parent
    # (the abandoned bad version), we must NOT propose rolling back onto those bad rules again.
    repo = Repository.open(str(tmp_path / "p.db"))
    repo.ensure_strategy("ma", 5000.0)
    repo.publish_playbook("v2", "ma", 1000, {"rules": ["bad"]})
    repo.publish_playbook("ma-rollback-9", "ma", 2000, {"rules": ["good"]}, parent_version="v2")
    repo.retire_playbook("v2", 2000)
    seed_versioned(repo, "ma", "v2", 4, win=True, base_ts=1000, pid0=1)        # abandoned looks ok
    seed_versioned(repo, "ma", "ma-rollback-9", 4, win=False, base_ts=5000, pid0=101)  # rollback bad
    repo.rebuild_trade_outcomes()
    assert propose_rollback(repo, "ma", 10_000, min_trades=3) is None          # no oscillation
    repo.close()


def test_no_rollback_when_winrate_not_worse(tmp_path):
    # avg_pnl worse but win rate EQUAL -> not a robust degradation signal -> no rollback.
    repo = Repository.open(str(tmp_path / "p.db"))
    repo.ensure_strategy("ma", 5000.0)
    repo.publish_playbook("v1", "ma", 1000, {"rules": ["a"]})
    repo.publish_playbook("v2", "ma", 2000, {"rules": ["b"]}, parent_version="v1")
    repo.retire_playbook("v1", 2000)
    seed_mixed(repo, "ma", "v1", [10, 10, -5, -5], 1000, 1)      # win_rate .5, avg +2.5
    seed_mixed(repo, "ma", "v2", [10, 10, -12, -12], 5000, 101)  # win_rate .5 (equal), avg -1.0
    repo.rebuild_trade_outcomes()
    assert propose_rollback(repo, "ma", 10_000, min_trades=3) is None
    repo.close()


def test_disconfirmation_flags_regimes_current_stopped_trading(tmp_path):
    repo = Repository.open(str(tmp_path / "p.db"))
    repo.ensure_strategy("ma", 5000.0)
    repo.publish_playbook("v1", "ma", 1000, {"rules": ["trade everything"]})
    repo.publish_playbook("v2", "ma", 2000, {"rules": ["skip chop"]}, parent_version="v1")
    repo.retire_playbook("v1", 2000)
    seed_versioned(repo, "ma", "v1", 3, win=True, base_ts=1000, pid0=1, regime="trend_up")
    seed_versioned(repo, "ma", "v1", 3, win=False, base_ts=2000, pid0=11, regime="chop")
    seed_versioned(repo, "ma", "v2", 3, win=True, base_ts=5000, pid0=101, regime="trend_up")
    repo.rebuild_trade_outcomes()                          # v2 never traded chop
    flags = SelfQuery(repo).disconfirmation_flags("ma", as_of=10_000)
    assert flags == [{"regime": "chop", "parent_trades": 3}]
    repo.close()
