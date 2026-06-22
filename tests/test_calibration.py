"""Phase-4 #8: confidence calibration report -> a proposed confidence floor.

Per-confidence-band realized win rate (mechanical, embargo-aware), and a proposer that — when the
low-confidence bands demonstrably underperform — FILES a human-gated param proposal raising the
confidence floor. It only proposes (never applies) and is idempotent against an identical pending
proposal.
"""
import json
from homing_trade.repository import Repository
from homing_trade.selfquery import SelfQuery
from homing_trade.calibration import propose_confidence_floor


def seed_trade(repo, strategy, pid, conf, win, ts):
    """One completed LONG round-trip entered at `conf` confidence; win => exit>entry =>
    prediction_correct=1 (mechanical, from prices)."""
    did = f"d{pid}"
    repo.log_decision(strategy, ts, ts, "LONG", conf, "r", {}, decision_id=did,
                      intended_action="LONG", taken_action="LONG")
    repo.record_trade(strategy, pid, "LONG", "OPEN", 100.0, 1, 0.1, -0.1, ts, decision_id=did)
    exit_px = 110.0 if win else 95.0
    repo.record_trade(strategy, pid, "LONG", "CLOSE", exit_px, 1, 0.1, exit_px - 100.0, ts + 100,
                      exit_reason="signal")


def seed_split(repo, strategy="ma"):
    """6 low-confidence losers (0.30) + 6 high-confidence winners (0.70), then build outcomes."""
    repo.ensure_strategy(strategy, 5000.0)
    pid = 1
    for _ in range(6):
        seed_trade(repo, strategy, pid, 0.30, win=False, ts=1000 + pid * 10); pid += 1
    for _ in range(6):
        seed_trade(repo, strategy, pid, 0.70, win=True, ts=1000 + pid * 10); pid += 1
    repo.rebuild_trade_outcomes()


def test_calibration_report_buckets_by_confidence(tmp_path):
    repo = Repository.open(str(tmp_path / "c.db"))
    seed_split(repo)
    cal = {b["band"]: b for b in SelfQuery(repo).confidence_calibration("ma", as_of=10_000)}
    assert cal["0.20-0.40"]["n"] == 6 and cal["0.20-0.40"]["win_rate"] == 0.0
    assert cal["0.60-0.80"]["n"] == 6 and cal["0.60-0.80"]["win_rate"] == 1.0
    assert cal["0.40-0.60"]["n"] == 0                       # empty band
    repo.close()


def test_calibration_embargo_hides_unrealized(tmp_path):
    repo = Repository.open(str(tmp_path / "c.db"))
    seed_split(repo)
    # as_of before any close realizes -> no rows in any band
    cal = SelfQuery(repo).confidence_calibration("ma", as_of=500)
    assert all(b["n"] == 0 for b in cal)
    repo.close()


def test_proposer_files_floor_when_low_bands_underperform(tmp_path):
    repo = Repository.open(str(tmp_path / "c.db"))
    seed_split(repo)
    pid = propose_confidence_floor(repo, "ma", 10_000, min_band_n=5)
    assert pid is not None
    p = repo.get_proposal(pid)
    assert p["kind"] == "param" and p["status"] == "pending"
    assert json.loads(p["payload_json"])["confidence_floor"] == 0.6   # lowest clearing band's lo
    repo.close()


def test_proposer_idempotent(tmp_path):
    repo = Repository.open(str(tmp_path / "c.db"))
    seed_split(repo)
    assert propose_confidence_floor(repo, "ma", 10_000, min_band_n=5) is not None
    assert propose_confidence_floor(repo, "ma", 20_000, min_band_n=5) is None   # same floor pending
    assert len(repo.pending_proposals("ma")) == 1
    repo.close()


def test_proposer_does_not_stack_a_different_floor(tmp_path):
    # A confidence-floor proposal already pending (even at a DIFFERENT value) blocks a new one,
    # so distinct floors can't accumulate on the cadence — the human clears it first.
    repo = Repository.open(str(tmp_path / "c.db"))
    seed_split(repo)
    repo.create_proposal("ma", "param", {"confidence_floor": 0.4}, "earlier floor", 5000)
    assert propose_confidence_floor(repo, "ma", 10_000, min_band_n=5) is None
    assert len(repo.pending_proposals("ma")) == 1            # still just the earlier one
    repo.close()


def test_proposer_noop_when_all_bands_clear(tmp_path):
    repo = Repository.open(str(tmp_path / "c.db"))
    repo.ensure_strategy("ma", 5000.0)
    for pid in range(1, 7):
        seed_trade(repo, "ma", pid, 0.70, win=True, ts=1000 + pid * 10)   # all winners
    repo.rebuild_trade_outcomes()
    assert propose_confidence_floor(repo, "ma", 10_000, min_band_n=5) is None  # nothing to fix
    assert repo.pending_proposals("ma") == []
    repo.close()


def test_proposer_respects_min_band_n(tmp_path):
    repo = Repository.open(str(tmp_path / "c.db"))
    seed_split(repo)
    # require 100 per band -> no band qualifies -> no proposal
    assert propose_confidence_floor(repo, "ma", 10_000, min_band_n=100) is None
    repo.close()


def test_proposer_noop_when_nothing_clears(tmp_path):
    repo = Repository.open(str(tmp_path / "c.db"))
    repo.ensure_strategy("ma", 5000.0)
    for pid in range(1, 7):
        seed_trade(repo, "ma", pid, 0.30, win=False, ts=1000 + pid * 10)   # all losers
    repo.rebuild_trade_outcomes()
    # a floor wouldn't help (no clearing band) -> don't propose
    assert propose_confidence_floor(repo, "ma", 10_000, min_band_n=5) is None
    repo.close()
