"""Phase 9 #1: the self-modification PR proposer pipeline.

A self-proposed code change is surfaced ONLY as a branch + PR (never committed to main, never merged),
and only after the guardrail (no protected paths) AND green tests pass; walk-forward results + the
provenance link are attached to the PR body. Tests use injected test/backtest/git/gh runners — fully
offline, no real git/gh."""
import pytest

from homing_trade import self_mod_proposer as smp


def _green():
    return {"passed": True, "summary": "719 passed"}


def _red():
    return {"passed": False, "summary": "2 failed, 717 passed"}


# --- the gate ---
def test_gate_blocks_protected_path_before_running_tests():
    calls = []
    rep = smp.gate_change(["homing_trade/skills/ma_trend.py", "homing_trade/risk.py"],
                          test_runner=lambda: calls.append(1) or _green())
    assert not rep["ok"] and rep["stage"] == "guardrail"
    assert "risk.py" in rep["reason"] and calls == []        # tests NOT run for an unsafe diff


def test_gate_blocks_empty_change():
    assert smp.gate_change([], test_runner=_green)["stage"] == "guardrail"


def test_gate_blocks_red_tests():
    rep = smp.gate_change(["homing_trade/skills/ma_trend.py"], test_runner=_red)
    assert not rep["ok"] and rep["stage"] == "tests"


def test_gate_passes_clean_safe_green():
    rep = smp.gate_change(["homing_trade/skills/macd.py"], test_runner=_green)
    assert rep["ok"] and rep["stage"] == "gated"


# --- the PR body ---
def test_pr_body_has_provenance_backtests_and_safety():
    body = smp.build_pr_body(rationale="tune RSI period", provenance="reflection #42",
                             backtests="ma_trend OOS +1.2%", test_summary="719 passed",
                             changed_paths=["homing_trade/skills/rsi_revert.py"])
    assert "reflection #42" in body
    assert "ma_trend OOS +1.2%" in body
    assert "no auto-merge" in body.lower() and "protected path" in body.lower()
    assert "`homing_trade/skills/rsi_revert.py`" in body


# --- the full pipeline ---
@pytest.mark.parametrize("branch", [
    "main", "master", "Main", "MAIN", " main ", "MASTER", "refs/heads/main",   # default-branch variants
    "feature/x", "hotfix",            # no self/ prefix
    "-D", "",                          # flag-like / empty
])
def test_propose_refuses_unsafe_branches(branch):
    git_calls, gh_calls = [], []
    r = smp.propose_pr(["homing_trade/skills/macd.py"], branch=branch, title="x", rationale="y",
                       test_runner=_green, git=lambda a: git_calls.append(a),
                       gh=lambda a: gh_calls.append(a) or "url", dry_run=False)
    assert not r["ok"] and r["stage"] == "branch" and r["pr_url"] is None
    assert git_calls == [] and gh_calls == []        # nothing created for an unsafe branch


def test_propose_refuses_flag_like_title():
    r = smp.propose_pr(["homing_trade/skills/macd.py"], branch="self/x", title="--amend",
                       rationale="y", test_runner=_green)
    assert not r["ok"] and r["stage"] == "title"


def test_propose_stages_only_declared_paths_not_whole_tree():
    # the committed diff must be EXACTLY the gated set — never `git add -A` (which could sweep in a
    # dirty protected file the gate never saw)
    git_calls = []
    smp.propose_pr(["homing_trade/skills/macd.py", "tests/test_new_algos.py"], branch="self/x",
                   title="tune", rationale="y", test_runner=_green,
                   git=lambda a: git_calls.append(a), gh=lambda a: "url", dry_run=False)
    add = next(c for c in git_calls if c and c[0] == "add")
    assert add == ["add", "--", "homing_trade/skills/macd.py", "tests/test_new_algos.py"]
    assert not any("-A" in c for c in git_calls)     # never stage the whole tree


def test_propose_protected_path_opens_nothing():
    git_calls, gh_calls = [], []
    r = smp.propose_pr(["homing_trade/live_broker.py"], branch="self/x", title="x", rationale="y",
                       test_runner=_green, git=lambda a: git_calls.append(a),
                       gh=lambda a: gh_calls.append(a) or "url", dry_run=False)
    assert not r["ok"] and r["stage"] == "guardrail"
    assert git_calls == [] and gh_calls == []                # nothing created on an unsafe diff


def test_propose_red_tests_opens_nothing():
    git_calls, gh_calls = [], []
    r = smp.propose_pr(["homing_trade/skills/macd.py"], branch="self/x", title="x", rationale="y",
                       test_runner=_red, git=lambda a: git_calls.append(a),
                       gh=lambda a: gh_calls.append(a) or "url", dry_run=False)
    assert not r["ok"] and r["stage"] == "tests"
    assert git_calls == [] and gh_calls == []


def test_propose_dry_run_builds_plan_without_side_effects():
    git_calls, gh_calls = [], []
    r = smp.propose_pr(["homing_trade/skills/macd.py"], branch="self/tune", title="tune",
                       rationale="why", test_runner=_green,
                       backtest_runner=lambda: "macd OOS +0.5%",
                       git=lambda a: git_calls.append(a), gh=lambda a: gh_calls.append(a) or "url")
    assert r["ok"] and r["dry_run"] and r["pr_url"] is None
    assert "macd OOS +0.5%" in r["body"]
    assert git_calls == [] and gh_calls == []                # dry-run touches nothing


def test_propose_apply_opens_branch_and_pr_never_merges():
    git_calls, gh_calls = [], []
    r = smp.propose_pr(["homing_trade/skills/macd.py"], branch="self/tune", title="tune",
                       rationale="why", provenance="proposal #7", test_runner=_green,
                       backtest_runner=lambda: "macd OOS +0.5%",
                       git=lambda a: git_calls.append(a),
                       gh=lambda a: gh_calls.append(a) or "https://github.com/x/y/pull/99",
                       dry_run=False)
    assert r["ok"] and r["pr_url"].endswith("/pull/99")
    # branch created, PR opened, in order; NEVER a merge COMMAND (the word may appear in the body)
    assert git_calls[0] == ["checkout", "-b", "self/tune"]
    assert any(c[:2] == ["pr", "create"] for c in gh_calls)
    assert not any(c[:2] == ["pr", "merge"] for c in gh_calls)        # no gh pr merge
    assert not any(c and "merge" in c[0] for c in git_calls)          # no git merge
    assert "proposal #7" in r["body"]


def test_propose_apply_requires_runners():
    with pytest.raises(ValueError):
        smp.propose_pr(["homing_trade/skills/macd.py"], branch="self/x", title="x", rationale="y",
                       test_runner=_green, dry_run=False)   # no git/gh injected
