"""Phase 3 #8: turn Discord #comms replies into proposal approvals.

`comms.read` gives the new human messages in the channel; this module parses approve/reject/status
commands out of them and drives the SAME human-approval gate the web UI uses (`decide_proposal` +
`ProposalApplier`), so an operator can approve a learn→correct proposal from chat, not only the web
UI. It is a CONSUMER of the gate — it never relaxes it: protected fields still can't be proposed,
approve still only flips a PENDING row, and only the playbook kind auto-applies.

Safety/robustness:
  - The command keyword must be the FIRST token (so "I don't approve of that" can't approve anything).
  - A cursor (`state['comms_after_id']`) advances past EVERY message seen each poll, so a reply is
    acted on exactly once and ordinary chatter is never reprocessed.
  - The poll NEVER raises into the trading loop (all errors are swallowed and reported as 0 actions).
"""
import time

from homing_trade import comms
from homing_trade.proposals import ProposalApplier, ProposalApplyError

# Require the EXPLICIT verb to mutate the gate — deliberately NO casual synonyms (ok/yes/no/👍),
# because "ok lets go with 3" or "no way 3" must NEVER be read as approve/reject 3. Status is
# read-only so its synonyms are harmless.
_APPROVE = {"approve", "approved"}
_REJECT = {"reject", "rejected"}
_STATUS = {"status", "pending", "list", "queue"}


def parse_command(text):
    """Parse a single chat message into (action, proposal_id) or None. action is
    'approve'|'reject'|'status'; proposal_id is an int, or None when absent (the keyword was there
    but no id). Returns None when the message is not a command at all."""
    if not text or not isinstance(text, str):
        return None
    tokens = text.strip().split()
    if not tokens:
        return None
    head = tokens[0].lstrip("/!").lower()      # tolerate "/approve", "!approve"
    if head in _STATUS:
        return ("status", None)
    if head in _APPROVE or head in _REJECT:
        action = "approve" if head in _APPROVE else "reject"
        pid = None
        for t in tokens[1:]:
            digits = t.lstrip("#").rstrip(".,)")
            if digits.isdigit():
                pid = int(digits)
                break
        return (action, pid)
    return None


def _status_text(repo):
    pend = repo.pending_proposals()
    if not pend:
        return "No pending proposals. ✅"
    lines = [f"{len(pend)} pending proposal(s) — reply `approve <id>` or `reject <id>`:"]
    for p in pend[:15]:
        strat = p.get("strategy") or "-"
        rat = (p.get("rationale") or "").strip().replace("\n", " ")
        lines.append(f"• #{p['id']} [{p.get('kind')}/{strat}] {rat[:90]}")
    return "\n".join(lines)


def apply_command(repo, command, *, now_ms, decided_by="human:discord"):
    """Execute one parsed command against the approval gate. Returns the human-readable reply text
    to post back (or None for a no-op). Mirrors the web UI's decide→apply flow."""
    action, pid = command
    if action == "status":
        return _status_text(repo)
    if pid is None:
        return f"Which proposal? Reply `{action} <id>` (e.g. `{action} 7`). Send `status` to list."
    if action == "reject":
        ok = repo.decide_proposal(pid, "rejected", decided_by, now_ms)
        return f"❌ Proposal #{pid} rejected." if ok else f"Proposal #{pid} is not pending (already decided or unknown)."
    # approve → decide, then attempt the mechanical apply (only playbook auto-applies today)
    ok = repo.decide_proposal(pid, "approved", decided_by, now_ms)
    if not ok:
        return f"Proposal #{pid} is not pending (already decided or unknown)."
    try:
        result = ProposalApplier(repo).apply(pid, applied_by=decided_by, now_ms=now_ms)
        return f"✅ Proposal #{pid} approved and applied ({result})."
    except (ProposalApplyError, ValueError) as exc:
        # Approved stands; just not auto-applied (kind not wired, or a guard re-tripped at apply).
        return f"✅ Proposal #{pid} approved (not auto-applied: {exc})."


class CommsApprovalRunner:
    """Self-gated periodic poll of #comms, run each engine tick like the reflection/research jobs.
    No-op unless inbound is configured; never raises into the trading loop."""

    def __init__(self, repo, cfg, *, reader=None, poster=None, clock=None):
        self.repo = repo
        self.cfg = cfg
        self._read = reader or comms.read
        self._post = poster or comms.post
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._last_poll_ms = 0

    def run(self):
        """Poll if the cadence has elapsed and inbound is enabled. Returns the number of commands
        acted on this call (0 on skip/error). Swallows ALL errors — a comms hiccup must never
        break the trading tick."""
        try:
            if not getattr(self.cfg, "comms_inbound_enabled", False):
                return 0                          # opt-in: off by default (and keeps tests offline)
            if not comms.inbound_enabled():
                return 0
            now = self._clock()
            if now - self._last_poll_ms < getattr(self.cfg, "comms_poll_sec", 30) * 1000:
                return 0
            self._last_poll_ms = now
            return self.poll_once(now)
        except Exception:
            return 0

    def poll_once(self, now_ms):
        """Read new messages since the cursor, act on commands, advance the cursor past ALL of them
        (so chatter isn't reprocessed and each command fires exactly once)."""
        cursor = self.repo.get_state("comms_after_id") or None
        msgs = self._read(after_id=cursor)
        if not msgs:
            return 0
        acted = 0
        for m in msgs:
            try:
                cmd = parse_command(m.get("content", ""))
                if cmd is not None:
                    author = m.get("author", "") or ""
                    reply = apply_command(self.repo, cmd, now_ms=now_ms,
                                          decided_by=f"human:discord:{author}"[:64])
                    if reply:
                        self._post(reply)
                        acted += 1
            except Exception:
                # one malformed/poison message must never wedge the cursor or the whole queue.
                pass
        self.repo.set_state("comms_after_id", str(msgs[-1]["id"]))   # oldest-first → last = highest id
        return acted
