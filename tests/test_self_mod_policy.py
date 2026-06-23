"""Phase 9 #3: the self-modification merge policy — human-merged only, after green CI.

Covers the merge-command guard (rejects git merge / gh pr merge / --auto / --admin, allows the
proposer's real branch+commit+push+pr-create plan) and the fail-closed CI gate predicate."""
import pytest

from homing_trade import self_mod_policy as pol


# --- the merge-command guard ---
@pytest.mark.parametrize("cmd", [
    ["merge", "origin/main"],                    # git merge
    ["pr", "merge", "77"],                        # gh pr merge
    ["pr", "merge", "77", "--squash"],
    ["pr", "merge", "--auto", "77"],             # queue auto-merge
    ["pr", "create", "--title", "x", "--admin"], # admin bypass-checks override
    ["pr", "merge", "--admin"],
    ["pull", "origin", "main"],                  # git pull = fetch + merge
    ["pr", "merge", "--auto=true"],              # =value spelling must not slip past
    ["pr", "create", "--admin=1"],
    ["PR", "MERGE", "77"],                        # case-insensitive
])
def test_merge_like_commands_are_refused(cmd):
    assert pol._is_merge_command(cmd)
    with pytest.raises(PermissionError):
        pol.assert_no_merge_commands([["checkout", "-b", "self/x"], cmd])


@pytest.mark.parametrize("cmd", [
    ["checkout", "-b", "self/tune"],
    ["add", "--", "homing_trade/skills/macd.py"],
    ["commit", "-m", "tune macd"],
    ["commit", "-m", "resolve merge conflict notes"],  # 'merge' in a message is not a merge command
    ["push", "-u", "origin", "self/tune"],
    ["pr", "create", "--title", "x", "--body", "y"],
    [],
])
def test_benign_commands_pass(cmd):
    assert not pol._is_merge_command(cmd)


def test_assert_passes_the_full_proposer_plan():
    plan = [["checkout", "-b", "self/x"], ["add", "--", "f.py"], ["commit", "-m", "t"],
            ["push", "-u", "origin", "self/x"], ["pr", "create", "--title", "t", "--body", "b"]]
    assert pol.assert_no_merge_commands(plan) is True


# --- the CI gate predicate (fail-closed) ---
def test_ci_gate_open_only_on_passing_required_check():
    assert pol.ci_gate_satisfied([{"name": "tests", "state": "SUCCESS"}])
    assert pol.ci_gate_satisfied([{"name": "tests", "state": "pass"},
                                  {"name": "other", "state": "FAILURE"}])


@pytest.mark.parametrize("checks", [
    [],
    None,
    [{"name": "tests", "state": "FAILURE"}],
    [{"name": "tests", "state": "PENDING"}],
    [{"name": "tests", "state": ""}],
    [{"name": "lint", "state": "SUCCESS"}],          # required check absent -> not satisfied
    [{"name": "other", "state": "SUCCESS"}],
])
def test_ci_gate_fails_closed(checks):
    assert not pol.ci_gate_satisfied(checks)


def test_required_check_name_matches_ci_workflow():
    # the gate's required check must be the job name the CI workflow actually publishes
    import pathlib
    ci = (pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text()
    assert f"name: {pol.REQUIRED_CHECK}" in ci
