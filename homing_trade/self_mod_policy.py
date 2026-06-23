"""Phase 9 #3: the merge policy for self-modification PRs.

A self-proposed CODE change is surfaced as a branch + PR and merged ONLY by a human, ONLY after CI
is green. This module is the single, testable statement of that policy — the proposer pipeline and
any future git/gh-driving tool enforce it through here rather than re-stating it:

  - merge is HUMAN-ONLY: no automated step may merge or enable auto-merge. `assert_no_merge_commands`
    refuses a `git merge`, a `gh pr merge`, an `--auto` (queue auto-merge), or an `--admin`
    (bypass-checks override) anywhere in the commands a proposer issues. self_mod_proposer runs this
    over the EXACT command plan it is about to execute, so a future edit that appends a merge is
    caught structurally — not just by a hand-written test.
  - CI is a HARD gate: the `tests` check must pass before merge. `ci_gate_satisfied` is the
    fail-closed predicate (a missing or non-passing required check is NOT satisfied). Repo-side
    enforcement (actually BLOCKING the merge button on red) is branch protection marking `tests`
    Required — a one-time manual setting documented in docs/self-mod-merge-policy.md; this module
    is the in-code half that a tool consults and that tests pin.
"""

REQUIRED_CHECK = "tests"
# gh reports a passing check state as one of these (REST/GraphQL vs `gh pr checks` differ).
_PASS_STATES = frozenset({"pass", "success", "completed_success", "neutral_success"})
# git verbs that merge: `merge` outright, and `pull` (a fetch + merge by default).
_MERGE_VERBS = frozenset({"merge", "pull"})
# Flags that turn any command into a merge: queue auto-merge, or bypass-checks admin override.
_MERGE_FLAGS = frozenset({"--auto", "--admin"})


def _is_merge_command(cmd):
    """True if this single arg-list would merge or enable auto-merge. Recognises `git merge` and
    `git pull` (pull = fetch + merge) verbs, `gh pr merge` (subcommand), and the --auto/--admin
    override flags on ANY command — including the `--flag=value` spelling, so the guard stays
    correct if reused by a tool whose flags take values."""
    if not cmd:
        return False
    toks = [str(a).lower() for a in cmd]
    if toks[0] in _MERGE_VERBS:                                  # git merge / git pull
        return True
    if toks[0] == "pr" and len(toks) > 1 and toks[1] == "merge":  # gh pr merge ...
        return True
    # strip any `=value` so `--admin=1` / `--auto=true` can't slip past exact-token matching
    return any(t.split("=", 1)[0] in _MERGE_FLAGS for t in toks)


def assert_no_merge_commands(commands):
    """Raise PermissionError if ANY command would merge or enable auto-merge. `commands` is the
    list of arg-lists (git and/or gh) a proposer issued or is about to issue. Returns True when the
    whole plan is merge-free."""
    bad = [c for c in commands if _is_merge_command(c)]
    if bad:
        raise PermissionError(
            "self-modification must never merge or auto-merge — a human merges after CI. "
            f"Refused command(s): {bad}")
    return True


def ci_gate_satisfied(checks, *, required=REQUIRED_CHECK):
    """Whether the CI gate permits a merge. FAILS CLOSED: returns True only if the `required`
    check is present AND in a passing state. A missing required check, a pending/failed check, or
    an empty/None list all return False — the gate never opens on absent evidence.

    `checks` is an iterable of dicts shaped like `gh pr checks --json name,state` output:
    `{"name": "tests", "state": "SUCCESS"|"FAILURE"|"PENDING"|...}`."""
    for c in checks or ():
        if (c.get("name") or "").strip() == required:
            return (c.get("state") or "").strip().lower() in _PASS_STATES
    return False
