"""Phase 7 #5: promotion discipline.

A variant is promoted only when, out-of-sample, it beats the baseline AND clears the pre-registered
MDE AND is significant after multiple-comparison correction over the search budget — and even then it
only FILES a human-gated strategy_toggle proposal (never auto-enables). Covers the corrections
(Bonferroni, Benjamini-Hochberg), the pure gate's four conditions + their precedence, and the
end-to-end orchestration on the experiments ledger (conclude + file/refrain, idempotency)."""
import json

import pytest

from homing_trade.promotion import (bonferroni_alpha, benjamini_hochberg, promotion_decision,
                                     evaluate_and_maybe_promote)
from homing_trade.repository import Repository
from homing_trade.proposals import ProposalApplier, ProposalApplyError


# --- corrections ---
def test_bonferroni_alpha():
    assert bonferroni_alpha(0.05, 5) == pytest.approx(0.01)
    assert bonferroni_alpha(0.05, 0) == pytest.approx(0.05)     # guards n_tests=0 -> /1


def test_benjamini_hochberg_rejects_step_up():
    # m=5, alpha=.05 -> rank thresholds .01,.02,.03,.04,.05. Largest passing rank is 2 (.008<=.02),
    # so BH rejects the two smallest p's and nothing larger.
    pvals = [0.2, 0.001, 0.3, 0.008, 0.4]
    reject, thr = benjamini_hochberg(pvals, 0.05)
    assert reject == [False, True, False, True, False]
    assert thr == pytest.approx(2 / 5 * 0.05)


def test_benjamini_hochberg_step_up_rejects_below_largest_passing():
    # step-UP: even a p that fails its OWN rank threshold is rejected if a LARGER rank passes.
    # ranks .01,.02,.03,.04,.05; .039 fails .03 but rank-5 .049<=.05 passes -> kmax=5 -> all reject.
    reject, thr = benjamini_hochberg([0.001, 0.008, 0.039, 0.041, 0.049], 0.05)
    assert reject == [True, True, True, True, True] and thr == pytest.approx(0.05)


def test_benjamini_hochberg_none_pass():
    reject, thr = benjamini_hochberg([0.9, 0.8], 0.05)
    assert reject == [False, False] and thr == 0.0


def test_benjamini_hochberg_empty():
    assert benjamini_hochberg([], 0.05) == ([], 0.0)


# --- the pure gate ---
def _strong():  # all four conditions pass
    return dict(diff=0.8, mde=0.5, p_value=0.001, n_a=40, n_b=40, alpha=0.05, search_budget=1)


def test_gate_promotes_when_all_conditions_hold():
    v = promotion_decision(**_strong())
    assert v["promote"] and v["won_oos"] and v["clears_mde"] and v["significant"] and v["enough_samples"]


def test_gate_blocks_on_insufficient_samples_first():
    v = promotion_decision(**{**_strong(), "n_a": 5})
    assert not v["promote"] and "insufficient OOS samples" in v["reason"]


def test_gate_blocks_when_challenger_loses():
    v = promotion_decision(**{**_strong(), "diff": -0.1})
    assert not v["promote"] and not v["won_oos"] and "did not beat baseline" in v["reason"]


def test_gate_blocks_below_mde():
    v = promotion_decision(**{**_strong(), "diff": 0.3, "mde": 0.5})
    assert not v["promote"] and not v["clears_mde"] and "below the pre-registered MDE" in v["reason"]


def test_gate_blocks_when_not_significant_after_correction():
    # p=0.02 passes alpha .05 alone, but Bonferroni over 10 tests needs < .005
    v = promotion_decision(**{**_strong(), "p_value": 0.02, "search_budget": 10})
    assert not v["promote"] and not v["significant"]
    assert v["corrected_alpha"] == pytest.approx(0.005)


def test_gate_mde_unset_blocks():
    v = promotion_decision(**{**_strong(), "mde": None})
    assert not v["promote"] and not v["clears_mde"]


# --- end-to-end orchestration on the ledger ---
def _samples(mean, n=40, spread=0.05):
    # tight, clearly-separated samples so the t-test is decisive; small deterministic jitter
    return [mean + (i % 5) * spread - 2 * spread for i in range(n)]


def _moderate(mean, n=40):
    # wider spread -> a marginal edge (diff 0.12, two-sided p~0.0023) that promotes at budget=1 but
    # is blocked once Bonferroni corrects over a large search budget
    base = [-0.27, -0.18, -0.09, 0.0, 0.09, 0.18, 0.27, 0.0]
    return [mean + base[i % len(base)] for i in range(n)]


def test_evaluate_promotes_and_files_human_gated_proposal(tmp_path):
    repo = Repository.open(str(tmp_path / "p.db"))
    eid = repo.create_experiment("supertrend>ma_trend on pnl_pct", "supertrend", "ma_trend",
                                 "pnl_pct", start_ts=1000, mde=0.5)
    v = evaluate_and_maybe_promote(repo, eid, _samples(1.5), _samples(0.2), now_ms=5000)
    assert v["promote"] and v["proposal_id"] is not None
    exp = repo.get_experiment(eid)
    assert exp["status"] == "concluded" and exp["result"] == "promote"
    assert exp["correction_method"] == "bonferroni" and exp["n_a"] == 40
    # the proposal is human-gated (pending) and NEVER auto-enables, even on approve
    p = repo.get_proposal(v["proposal_id"])
    assert p["kind"] == "strategy_toggle" and p["status"] == "pending"
    payload = json.loads(p["payload_json"])
    assert payload["strategy"] == "supertrend" and payload["action"] == "enable"
    assert set(payload.keys()) == {"strategy", "action", "description"}
    repo.decide_proposal(v["proposal_id"], "approved", "human:test", 6000)
    with pytest.raises(ProposalApplyError):
        ProposalApplier(repo).apply(v["proposal_id"], applied_by="human:test", now_ms=7000)
    repo.close()


def test_evaluate_refuses_weak_edge_files_nothing(tmp_path):
    repo = Repository.open(str(tmp_path / "p.db"))
    eid = repo.create_experiment("h", "challenger", "baseline", "pnl_pct", start_ts=1000, mde=0.5)
    v = evaluate_and_maybe_promote(repo, eid, _samples(0.25), _samples(0.2), now_ms=5000)
    assert not v["promote"] and v["proposal_id"] is None
    assert not v["clears_mde"]                       # specifically the MDE bar blocked it (0.05 < 0.5)
    assert repo.get_experiment(eid)["result"] == "inconclusive"
    assert [p for p in repo.pending_proposals() if p["kind"] == "strategy_toggle"] == []
    repo.close()


def test_search_budget_bonferroni_blocks_marginal_edge_end_to_end(tmp_path):
    repo = Repository.open(str(tmp_path / "p.db"))
    # budget=1: the marginal edge clears MDE and is significant -> promotes
    e1 = repo.create_experiment("h", "supertrend", "ma_trend", "pnl_pct", start_ts=100, mde=0.1)
    v1 = evaluate_and_maybe_promote(repo, e1, _moderate(0.62), _moderate(0.50), now_ms=5000)
    assert v1["promote"] and v1["proposal_id"] is not None and v1["search_budget"] == 1
    # inflate the search budget with many other experiments started in-window -> Bonferroni tightens
    for i in range(60):
        repo.create_experiment(f"d{i}", "x", "y", "pnl_pct", start_ts=200 + i)
    e2 = repo.create_experiment("h2", "zscore_revert", "rsi_revert", "pnl_pct", start_ts=300, mde=0.1)
    v2 = evaluate_and_maybe_promote(repo, e2, _moderate(0.62), _moderate(0.50), now_ms=5000)
    assert v2["search_budget"] >= 60
    assert not v2["promote"] and not v2["significant"] and v2["clears_mde"]   # only significance failed
    assert v2["proposal_id"] is None and repo.get_experiment(e2)["result"] == "inconclusive"
    repo.close()


def test_rejected_promotion_not_refiled_within_cooldown(tmp_path):
    repo = Repository.open(str(tmp_path / "p.db"))
    e1 = repo.create_experiment("h", "supertrend", "ma_trend", "pnl_pct", start_ts=100, mde=0.5)
    v1 = evaluate_and_maybe_promote(repo, e1, _samples(1.5), _samples(0.2), now_ms=1000)
    repo.decide_proposal(v1["proposal_id"], "rejected", "human:test", 2000)
    # a fresh experiment promoting the SAME challenger inside the cooldown -> filed nothing
    e2 = repo.create_experiment("h2", "supertrend", "donchian", "pnl_pct", start_ts=300, mde=0.5)
    v2 = evaluate_and_maybe_promote(repo, e2, _samples(1.5), _samples(0.2), now_ms=3000)
    assert v2["promote"] and v2["proposal_id"] is None and "rejected recently" in v2["reason"]
    # after the cooldown, new evidence may re-propose
    e3 = repo.create_experiment("h3", "supertrend", "macd", "pnl_pct", start_ts=400, mde=0.5)
    v3 = evaluate_and_maybe_promote(repo, e3, _samples(1.5), _samples(0.2),
                                    now_ms=2000 + 7 * 86_400_000 + 1)
    assert v3["proposal_id"] is not None
    repo.close()


def test_evaluate_is_idempotent_on_concluded(tmp_path):
    repo = Repository.open(str(tmp_path / "p.db"))
    eid = repo.create_experiment("h", "supertrend", "ma_trend", "pnl_pct", start_ts=1000, mde=0.5)
    evaluate_and_maybe_promote(repo, eid, _samples(1.5), _samples(0.2), now_ms=5000)
    again = evaluate_and_maybe_promote(repo, eid, _samples(1.5), _samples(0.2), now_ms=6000)
    assert again.get("already_concluded") and again["proposal_id"] is None
    repo.close()


def test_evaluate_does_not_duplicate_pending_promotion(tmp_path):
    repo = Repository.open(str(tmp_path / "p.db"))
    e1 = repo.create_experiment("h1", "supertrend", "ma_trend", "pnl_pct", start_ts=1000, mde=0.5)
    v1 = evaluate_and_maybe_promote(repo, e1, _samples(1.5), _samples(0.2), now_ms=5000)
    assert v1["proposal_id"] is not None
    # a second experiment promoting the SAME challenger while the first proposal is still pending
    e2 = repo.create_experiment("h2", "supertrend", "donchian", "pnl_pct", start_ts=2000, mde=0.5)
    v2 = evaluate_and_maybe_promote(repo, e2, _samples(1.5), _samples(0.2), now_ms=6000)
    assert v2["promote"] and v2["proposal_id"] is None and "already pending" in v2["reason"]
    toggles = [p for p in repo.pending_proposals() if p["kind"] == "strategy_toggle"]
    assert len(toggles) == 1
    repo.close()


def test_evaluate_too_few_samples_concludes_inconclusive(tmp_path):
    repo = Repository.open(str(tmp_path / "p.db"))
    eid = repo.create_experiment("h", "a", "b", "pnl_pct", start_ts=1000, mde=0.5)
    v = evaluate_and_maybe_promote(repo, eid, [1.0], [0.5], now_ms=5000)
    assert not v["promote"] and repo.get_experiment(eid)["result"] == "inconclusive"
    repo.close()
