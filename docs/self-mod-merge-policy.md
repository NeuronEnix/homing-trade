# Self-modification merge policy (Phase 9 #3)

A self-proposed **code** change is surfaced as a branch + PR and **merged only by a human, only after CI is green.** Nothing in the autonomous loop merges its own code. This document is the human-facing statement of that policy; `homing_trade/self_mod_policy.py` is the in-code, test-pinned half.

## The two invariants

1. **Merge is human-only — no auto-merge.**
   - `self_mod_proposer.propose_pr` opens a branch + PR and **never merges**. There is no `gh pr merge` / `git merge` call in the pipeline.
   - This is enforced *structurally*, not just by convention: `propose_pr` builds the exact git/gh command plan and runs it through `self_mod_policy.assert_no_merge_commands` **before executing anything**. A future edit that appends a `git merge`, `gh pr merge`, `--auto` (merge-queue), or `--admin` (bypass-checks override) is refused before a single command runs. `test_self_mod_policy.py` + `test_self_mod_proposer.py` pin this.

2. **CI is a hard gate — a green `tests` check before merge.**
   - `.github/workflows/ci.yml` runs the full suite (`pytest -q`) and the ROADMAP consistency check on every PR. The job is named `tests`.
   - `self_mod_policy.ci_gate_satisfied(checks)` is the fail-closed predicate a human/tool consults: it returns `True` **only** when the required `tests` check is present and passing. A missing, pending, or failed check → not satisfied. The gate never opens on absent evidence.

## The one manual step (repo setting)

The workflow makes a red suite *visible*, but only **branch protection** makes it *blocking*. To actually disable the merge button while CI is red:

> **Settings → Branches → Branch protection rules → `main` → Require status checks to pass before merging → add `tests`.**

While that box is unchecked, CI is advisory (visible, not enforced). This is the only part of the policy that cannot live in the repo — flag it to the operator until set.

## Why this lives behind a human

Phase 9 lets the bot *propose* code, never *apply* it. The protected-paths guardrail (`self_modify.py`, Phase 9 #2) keeps a proposal off the safety perimeter (risk / kill-switch / secrets / live-arming); the proposer (#1) makes a branch+PR the only path; provenance (#4) ties every PR back to the reflection/proposal that motivated it; and this merge policy (#3) keeps the final commit-to-`main` decision with a human, after CI. Real-money arming is a separate, later, explicitly human-gated milestone (Phase 10).
