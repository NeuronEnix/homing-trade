"""The APPLY step (Phase-4 #7): turn a human-APPROVED proposal into a real change.

This is the second half of the approval gate. `create_proposal` (db.py) refuses to even record
a proposal that touches a protected zone (risk limits / kill-switch / leverage / sizing /
execution fidelity / live-arming / secrets); `decide_proposal` records a human's approve/reject;
and `ProposalApplier.apply` here is the ONLY place an approved suggestion becomes effective.

Invariants:
  - Applies ONLY an `approved`, not-yet-applied proposal (never a pending/rejected one, and
    never twice — idempotent).
  - RE-ASSERTS the protected-fields guard at apply time (defense in depth): even if a protected
    payload somehow reached an approved row out-of-band, it can never be applied.
  - Records provenance (applied_ts / applied_by / applied_result) so the effect is auditable.

Supported kinds:
  - playbook — publishes the new immutable version and supersedes the current active one (retire
    + true-lineage parent_version). This is exactly what `llm_trader` injects (#6), so approving
    a playbook proposal closes the learn->correct loop end-to-end.
  - param / prompt / strategy_toggle — recognised but NOT auto-applied yet (each needs a runtime
    override store its consumer reads); apply() refuses them explicitly rather than silently
    no-op'ing, so the gate never lies about what took effect.
"""
import json
import sqlite3

from homing_trade.db import _assert_no_protected_fields


class ProposalApplyError(Exception):
    """Raised when a proposal cannot be applied (not approved, already applied, unknown,
    unsupported kind, or a malformed payload)."""


class ProposalApplier:
    def __init__(self, repo):
        self.repo = repo

    def apply(self, proposal_id, *, applied_by, now_ms):
        """Apply one approved proposal. Returns a kind-specific result (the published playbook
        version for a playbook). Raises ProposalApplyError on a gate violation, or ValueError
        (from the protected guard) if the payload touches a protected zone."""
        p = self.repo.get_proposal(proposal_id)
        if p is None:
            raise ProposalApplyError(f"no such proposal: {proposal_id}")
        if p["status"] != "approved":
            raise ProposalApplyError(
                f"proposal {proposal_id} is '{p['status']}', not 'approved' — apply requires "
                "an explicit human approval first")
        if p.get("applied_ts") is not None:
            raise ProposalApplyError(f"proposal {proposal_id} was already applied "
                                     f"at {p['applied_ts']}")
        payload = json.loads(p["payload_json"])
        # Defense in depth: the same guard create_proposal uses, re-run at apply time. A protected
        # field can never take effect even if it slipped past creation (out-of-band INSERT, a
        # future bug in the gate, etc.). scan_values mirrors create_proposal: params set config
        # fields (scan values for field-as-value), while playbook/prompt VALUES are non-actuating
        # prose (a rule string can't mutate config — leverage/risk/etc. are gated at every config
        # path), so key-only scanning is correct and intentional for them.
        _assert_no_protected_fields(payload, scan_values=(p["kind"] == "param"))

        if p["kind"] == "playbook":
            return self._apply_playbook(p, payload, now_ms, applied_by)
        raise ProposalApplyError(
            f"apply for kind '{p['kind']}' is not wired yet (needs a runtime override store its "
            "consumer reads); approve records intent but the change is not auto-applied")

    def _apply_playbook(self, p, payload, now_ms, applied_by):
        """Publish the proposed playbook as the new active version for the strategy and supersede
        whatever is currently active — atomically with stamping the proposal applied (see
        repo.apply_playbook_proposal). Validates strategy/version/rules up front so the only
        IntegrityError that can surface is an unambiguous duplicate-version (PK)."""
        strategy = p["strategy"]
        version = payload.get("version")
        rules_raw = payload.get("rules")
        if not isinstance(strategy, str) or not strategy.strip():
            raise ProposalApplyError("playbook proposal has no strategy")
        if not isinstance(version, str) or not version.strip():
            raise ProposalApplyError("playbook payload needs a non-empty 'version'")
        # Persist only clean string rules (the immutable record shouldn't carry non-string junk).
        rules = ([r.strip() for r in rules_raw if isinstance(r, str) and r.strip()]
                 if isinstance(rules_raw, list) else [])
        if not rules:
            raise ProposalApplyError("playbook payload needs a non-empty 'rules' list of strings")
        try:
            result = self.repo.apply_playbook_proposal(p["id"], version, strategy, rules,
                                                       applied_by, now_ms)
        except sqlite3.IntegrityError as exc:   # version is a PK -> already published
            raise ProposalApplyError(f"playbook version '{version}' already exists") from exc
        if result is None:                       # re-verify inside the txn failed (concurrent apply)
            raise ProposalApplyError(
                f"proposal {p['id']} was no longer applicable (concurrently modified?)")
        return result
