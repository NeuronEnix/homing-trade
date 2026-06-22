"""Phase 6 #7: candidate-strategy intake.

A research scan FILES new algorithm ideas as human-gated strategy_toggle proposals — never enables
anything. Tests: parse + file, idempotency (no dup proposals across scans + within a batch), the
never-auto-enable + human-gate invariant, the protected-fields guard, degrade-safe (no fn / error /
garbled), the daily cadence runner, and the factory gating. Offline (injected research_fn).
"""
import json
from homing_trade.repository import Repository
from homing_trade.research import StrategyResearcher, ResearchRunner, build_research_fn
from homing_trade.config import Config


def _candidates(*names):
    return json.dumps({"candidates": [
        {"name": n, "description": f"{n} entry/exit", "rationale": f"why {n}"} for n in names]})


def _pending_toggles(repo):
    return [p for p in repo.pending_proposals() if p["kind"] == "strategy_toggle"]


def test_files_new_candidates_as_strategy_toggle_proposals(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    r = StrategyResearcher(repo, lambda prompt: _candidates("supertrend", "ttm_squeeze"))
    filed = r.run_once(1000)
    assert len(filed) == 2
    toggles = _pending_toggles(repo)
    names = sorted(json.loads(p["payload_json"])["strategy"] for p in toggles)
    assert names == ["supertrend", "ttm_squeeze"]
    p = toggles[0]
    assert p["kind"] == "strategy_toggle" and p["status"] == "pending"   # human-gated, not applied
    assert json.loads(p["payload_json"])["action"] == "enable"
    repo.close()


def test_idempotent_across_scans(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    r = StrategyResearcher(repo, lambda prompt: _candidates("supertrend"))
    assert len(r.run_once(1000)) == 1
    assert r.run_once(2000) == []                       # already pending -> not re-filed
    assert len(_pending_toggles(repo)) == 1
    repo.close()


def test_dedup_within_a_batch(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    r = StrategyResearcher(repo, lambda prompt: _candidates("Supertrend", "supertrend", "rsi_revert"))
    filed = r.run_once(1000)                             # case-insensitive dup collapses
    assert len(filed) == 2 and len(_pending_toggles(repo)) == 2
    repo.close()


def test_respects_max_candidates(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    r = StrategyResearcher(repo, lambda prompt: _candidates("a", "b", "c", "d", "e"),
                           max_candidates=2)
    assert len(r.run_once(1000)) == 2
    repo.close()


def test_never_auto_enables_even_on_approve(tmp_path):
    # The whole safety point: filing + approving a strategy_toggle must NOT enable a strategy.
    from homing_trade.proposals import ProposalApplier, ProposalApplyError
    repo = Repository.open(str(tmp_path / "r.db"))
    pid = StrategyResearcher(repo, lambda prompt: _candidates("supertrend")).run_once(1000)[0]
    repo.decide_proposal(pid, "approved", "human:test", 2000)
    try:
        ProposalApplier(repo).apply(pid, applied_by="human:test", now_ms=3000)
        applied = True
    except ProposalApplyError:
        applied = False
    assert applied is False                              # strategy_toggle apply is refused
    repo.close()


def test_payload_keys_are_bot_fixed_not_candidate_controlled(tmp_path):
    # Safety: the candidate only supplies VALUES (name/description/rationale); the proposal payload
    # KEYS are fixed by us to a safe set, so a candidate can never smuggle a protected config field
    # (the create_proposal guard scans keys). A candidate even NAMED like a config field is just an
    # inert label in a value — filed, but it sets nothing and can't be auto-applied.
    repo = Repository.open(str(tmp_path / "r.db"))
    extra = json.dumps({"candidates": [
        {"name": "leverage", "description": "d", "rationale": "r", "max_daily_loss": 0}]})
    StrategyResearcher(repo, lambda prompt: extra).run_once(1000)
    p = _pending_toggles(repo)[0]
    payload = json.loads(p["payload_json"])
    assert set(payload.keys()) == {"strategy", "action", "description"}   # candidate's extra key dropped
    assert payload["strategy"] == "leverage" and payload["action"] == "enable"
    repo.close()


def test_degrades_no_fn_error_garbled(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    assert StrategyResearcher(repo, None).run_once(1000) == []                     # no fn
    def boom(prompt): raise RuntimeError("model down")
    assert StrategyResearcher(repo, boom).run_once(1000) == []                     # error
    assert StrategyResearcher(repo, lambda p: "not json").run_once(1000) == []     # garbled
    assert StrategyResearcher(repo, lambda p: '{"candidates": "nope"}').run_once(1000) == []
    assert _pending_toggles(repo) == []
    repo.close()


def test_prompt_excludes_existing_strategies(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    seen = {}
    def fetch(prompt): seen["p"] = prompt; return _candidates("new_one")
    StrategyResearcher(repo, fetch).run_once(1000, existing_strategies=["ma_trend", "rsi_revert"])
    assert "ma_trend" in seen["p"] and "rsi_revert" in seen["p"]
    repo.close()


# --- runner cadence + factory ---
def test_runner_is_cadence_gated(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    clock = [1000.0]
    calls = []
    def fetch(prompt): calls.append(1); return _candidates("supertrend")
    run = ResearchRunner(repo, fetch, poll_sec=3600, clock=lambda: clock[0])
    assert len(run.run()) == 1 and len(calls) == 1
    clock[0] += 100                                      # within cadence -> skipped
    assert run.run() == [] and len(calls) == 1
    clock[0] += 3600                                     # cadence due -> scans again (no new names)
    run.run()
    assert len(calls) == 2
    repo.close()


def test_runner_disabled_without_fn(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    run = ResearchRunner(repo, None)
    assert run.enabled is False and run.run() == []
    repo.close()


def test_build_research_fn_gating():
    assert build_research_fn(Config()) is None                          # default OFF
    assert build_research_fn(Config(research_enabled=True)) is not None  # enabled -> callable
