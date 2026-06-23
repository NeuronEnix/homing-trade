"""Self-modification PR proposer (Phase 9 #1).

When the bot proposes a CODE change to itself, this is the only sanctioned path to surface it: gate the
diff, then open it as a BRANCH + PR for a human to review and merge. It NEVER commits to main and NEVER
merges — a human does. The pipeline, in order (fail-fast, safety first):

  1. GUARDRAIL — `self_modify.protected_violations`: the diff must touch no protected path (risk /
     kill-switch / secrets / live-arming / dry-run / schema+guard / CI / the guardrail itself).
  2. GREEN TESTS — the injected `test_runner` must report the suite passing.
  3. WALK-FORWARD — the injected `backtest_runner` (optional) results are attached to the PR body.
  4. BRANCH + PR — via injected `git`/`gh` runners; the body carries the rationale, the provenance
     link (the reflections/proposals row that motivated it), the test summary, the backtest results,
     and the safety checklist. No merge, ever.

`dry_run=True` (the default) runs the gate + builds the PR body but does NOT touch git/gh — so calling
this never autonomously opens a PR. An operator enabling self-mod passes `dry_run=False`. git/gh are
injectable so the whole pipeline is unit-tested offline.
"""
from homing_trade.self_modify import protected_violations

_PROTECTED_BRANCHES = {"main", "master"}
_BRANCH_PREFIX = "self/"           # self-mod PRs must live on an identifiable, non-default branch


def _reject_branch(branch):
    """Reason to refuse a target branch, or None if it's an acceptable self-mod branch. Requiring a
    `self/` prefix robustly rejects EVERY default-branch variant (main/Main/MAIN/ main /
    refs/heads/main/master) — none start with the prefix — and a flag-like name."""
    if not isinstance(branch, str):
        return "branch must be a string"
    b = branch.strip()
    if not b or b.startswith("-"):
        return "empty or flag-like branch name"
    norm = b.lower()
    if norm.startswith("refs/heads/"):
        norm = norm[len("refs/heads/"):]
    if norm in _PROTECTED_BRANCHES:
        return f"refusing to propose onto the default branch '{b}'"
    if not b.startswith(_BRANCH_PREFIX):
        return f"self-mod branch must start with '{_BRANCH_PREFIX}' (got '{b}')"
    return None


def gate_change(changed_paths, *, test_runner):
    """Safety+quality gate for a proposed diff. Returns {ok, stage, reason, ...}. Checks the
    guardrail FIRST (cheapest + most important), then runs tests only if the diff is safe — so an
    unsafe diff never even triggers a test run. NEVER opens a PR."""
    if not changed_paths:
        return {"ok": False, "stage": "guardrail", "reason": "empty change (no files touched)"}
    violations = protected_violations(changed_paths)
    if violations:
        return {"ok": False, "stage": "guardrail", "violations": violations,
                "reason": "touches protected path(s): " + ", ".join(violations)}
    tests = test_runner() or {}
    if not tests.get("passed"):
        return {"ok": False, "stage": "tests", "reason": "test suite is not green", "tests": tests}
    return {"ok": True, "stage": "gated", "tests": tests}


def build_pr_body(*, rationale, provenance, backtests, test_summary, changed_paths):
    """The self-explanatory PR body. Always states the safety posture explicitly."""
    lines = ["## Self-proposed code change", "", rationale or "(no rationale given)", ""]
    lines.append(f"**Provenance:** {provenance}" if provenance
                 else "**Provenance:** (none — not linked to a reflection/proposal)")
    lines += ["", "**Files changed:**"] + [f"- `{p}`" for p in changed_paths]
    lines += ["", f"**Tests:** {test_summary or '(none reported)'}"]
    if backtests is not None:
        lines += ["", "**Walk-forward backtests:**", "```", str(backtests).strip(), "```"]
    lines += ["", "### Safety",
              "- Gated by `self_modify.protected_violations` — touches NO protected path "
              "(risk limits / kill-switch / secrets / live-arming / dry-run / schema+guard / CI).",
              "- Branch + PR only; **no auto-merge** — a human reviews and merges.",
              "- CI must pass before merge."]
    return "\n".join(lines)


def propose_pr(changed_paths, *, branch, title, rationale, test_runner, backtest_runner=None,
               provenance=None, git=None, gh=None, dry_run=True):
    """Gate a proposed diff and, if it passes, open it as a branch + PR (never merge). Returns a
    result dict. On a failed gate, returns early WITHOUT touching git/gh (nothing is created).

    git(args)->any / gh(args)->str are injected command runners. dry_run=True (default) builds the
    plan + body but performs NO git/gh side effects."""
    bad_branch = _reject_branch(branch)
    if bad_branch:
        return {"ok": False, "stage": "branch", "reason": bad_branch, "pr_url": None}
    if not isinstance(title, str) or not title.strip() or title.startswith("-"):
        return {"ok": False, "stage": "title", "reason": "empty or flag-like title", "pr_url": None}
    report = gate_change(changed_paths, test_runner=test_runner)
    if not report["ok"]:
        return {**report, "pr_url": None}
    backtests = backtest_runner() if backtest_runner else None
    body = build_pr_body(rationale=rationale, provenance=provenance, backtests=backtests,
                         test_summary=report["tests"].get("summary", ""), changed_paths=changed_paths)
    if dry_run:
        return {"ok": True, "dry_run": True, "stage": "planned", "branch": branch, "title": title,
                "body": body, "pr_url": None}
    if git is None or gh is None:
        raise ValueError("propose_pr(dry_run=False) needs git and gh runners")
    # Branch + commit + push + open PR. NO merge step exists in this function, by design.
    # Stage ONLY the validated paths (NOT `add -A`): the committed diff is then exactly the gated
    # set, so an under-declared dirty protected file can never be smuggled into the commit.
    git(["checkout", "-b", branch])
    git(["add", "--", *changed_paths])
    git(["commit", "-m", title])
    git(["push", "-u", "origin", branch])
    pr_url = gh(["pr", "create", "--title", title, "--body", body])
    return {"ok": True, "dry_run": False, "stage": "proposed", "branch": branch, "title": title,
            "body": body, "pr_url": pr_url}
