# DESIGN SPEC — Phase 11: Human-in-the-loop live control & notification

Status: **DESIGN (not built).** Sequenced after Phase 10 (real-money arming). Build only after this
doc is reviewed. Companion to the program design-of-record (`2026-06-22-self-sustaining-trading-program-design.md`).

## 0. Scope & relationship to other phases
- This phase is the **operator UX + control surface** for trading — how an end user stays *informed*
  of everything the bot does and *in control* of it, while the bot runs autonomously and only
  escalates when warranted.
- It is layered on existing primitives: the proposals approval-gate (`db.create_proposal` /
  `decide_proposal` / `ProposalApplier`), `comms` (read/post, now bot-first), `comms_approvals`
  (the inbound command consumer from Phase 3 #8), provenance, the replay/audit tool,
  `PROTECTED_PROPOSAL_FIELDS`, the `DailyRiskGuard` kill-switch, and the Phase-10 `arming` gate.
- It does **not** introduce real money — real orders remain behind the Phase-10 arming gate + live
  execution (#2). Phase 11 works fully on the **paper** feed first; the live feed is enabled only
  once Phase 10 is armed.
- Design principle: **the human is the authority.** The bot's job is to keep them fully in the
  know and to *ask* when uncertain or high-stakes — never to quietly assume.

## 1. Channels & identities
Two trade feeds (naming convention `<word>-trade`), plus the existing control channel:
- `#paper-trade` — paper activity. **Narrate-only, no approvals, just runs.** Outbound via a
  webhook (or the bot); high-verbosity allowed.
- `#live-trade` — real-money activity. **Interactive**: autonomous by default, escalates for
  approval on high-stakes/novel actions. Read+write by a dedicated bot.
- `#comms` (#approvals) — existing agent↔human control channel for proposal approvals (ht-dev).

Bots (see [[discord-bot-architecture]]):
- **ht-dev** — dev/proposal control plane (exists). Reads `#comms`.
- **ht-live** — money control plane (NEW). Own token + Message Content Intent, scoped to
  `#live-trade` ONLY. Independently killable; least-privilege so a leak can't touch code/proposals.

## 2. The escalation policy — "when must the bot ask?" (the crux)
A single, deterministic, **testable** function `escalation_for(action, ctx) -> Level` returning one
of `ROUTINE | NOTABLE | ESCALATION`. It is pure (decision-from-facts), logged, and replayable.
**Fail-safe: when any input is missing or the policy is unsure → escalate (never silently proceed).**

Triggers (an action escalates if ANY fires):
1. **Size** — order notional ≥ a configured % of live equity OR ≥ an absolute cap.
2. **Risk-posture change** — ANY change to leverage / `risk_pct` / daily caps. These are
   `PROTECTED_PROPOSAL_FIELDS`; they can never be auto-applied, so they ALWAYS escalate.
3. **Novelty ("doing something creative")** — the action is outside the brain's learned envelope.
   Concretely, escalate when, vs a rolling baseline of this strategy's recent live behavior:
   - a strategy/symbol/side it has not traded live before, or
   - size > (baseline mean + k·stdev), or
   - confidence in an unusual band (very low taken as a signal, or very high on a thin record), or
   - a brand-new strategy promoted from research/backtest going live for the first time.
   Novelty is computed mechanically from the audit ledger — never from model self-assessment.
4. **Drawdown / volatility guardrails** — approaching `max_daily_loss` (e.g. ≥ X% of the cap), a
   loss streak ≥ N, or a realized-vol spike beyond the risk-vol threshold.
5. **Arming / scale-up** — every Phase-10 arming step and every capital-cap increase escalates.

`NOTABLE` (heads-up, no approval needed): a first trade of the session, a regime flip, an exit at a
stop. `ROUTINE`: ordinary in-envelope entries/exits. Thresholds live in config (not protected, but
the *risk* fields they reference are).

## 3. Notification contract
Every message carries a consistent envelope: **what** (action, symbol, side, size) / **why** (the AI
thesis + the mechanical reason) / **risk** (notional, leverage, stop) / **decision_id** (so the
replay/audit tool can reconstruct it) / **level** (routine/notable/escalation). Rendered as a Discord
embed. Noise control: routine messages batched into a periodic digest; notable delivered promptly;
escalations delivered immediately and pinned until resolved.

## 4. Command protocol (inbound, `#live-trade`)
Extends the Phase-3-#8 `comms_approvals` parser/consumer with a money-control command set, **scoped
to `#live-trade` and read by ht-live**. Commands (explicit verbs only, owner-authenticated):
- `STOP-ALL` — trips the kill-switch immediately. **NEVER gated, queued, or delayed by the approval
  flow.** Highest priority; processed before anything else in a poll.
- `APPROVE <request-id>` / `REJECT <request-id>` — decide a specific escalation. Each escalation
  carries a **unique, single-use, expiring** id (replay-proof; a reused/expired id is rejected).
- `STATUS` / `QUERY` — current positions, equity, pending escalations, posture.
- `RESUME` — clear a pause/hold.
- `SET-POSTURE <conservative|normal|aggressive>` — see §5.

**Authentication (new, required for live):** only the configured owner Discord user id(s) may issue
control commands; every other author is ignored and logged. (Phase 3 #8 deliberately shipped without
this and stays default-OFF until this lands.)

**Timeouts & fail-safe defaults:** every escalation states its own "no answer ⇒ X" and a deadline.
If unanswered by the deadline, the **safe** branch fires (hold / don't open / shrink) — never the
risky one. Auth failures default-deny.

## 5. Risk-loosening ("be more aggressive") — handled with ceremony
Asymmetric by design:
- **Tightening / STOP** is always immediate and unconditional.
- **Loosening** (raise risk/leverage/posture) is: (a) bounded by a HARD code+human ceiling a chat
  reply can NEVER exceed; (b) requires an explicit **two-step confirm** (not a one-word message);
  (c) **auto-reverts** to normal posture after a bounded time/PnL window. Because leverage/`risk_pct`
  are protected, "aggressive" never directly mutates them — it selects among pre-vetted, bounded
  posture presets, each itself within the hard ceiling.

## 6. State machine & data model
Escalation lifecycle: `OPEN → (APPROVED | REJECTED | EXPIRED | SUPERSEDED)`. Reuse the proposals
gate where an escalation maps to a decision; add an `escalations` table (audit-truth-adjacent:
mechanically created, human-decided) recording id, created_ts, level, action payload, trigger(s),
deadline, decided_by, decided_ts, outcome, and the resulting effect — provenance-linked so the
replay tool reconstructs the full human-in-the-loop timeline (who approved what, when, why it asked).
A `live_commands` audit row records every inbound command, its author, and its effect.

## 7. Safety invariants (non-negotiable)
- The kill-switch (`STOP-ALL` / `max_daily_loss`) is always honored, immediately, and can never be
  gated or delayed by the approval flow.
- Loosening is always bounded (hard ceiling), confirmed, and temporary; tightening is always instant.
- Default-deny on timeouts and auth failures.
- Real money stays behind the Phase-10 arming gate; this phase is control UX on top.
- Everything auditable (audit-truth tables) + replayable + provenance-linked.
- ht-live is least-privilege and independently killable.

## 8. Rollout sequencing
1. Build & prove the whole notification → escalation → command UX on **`#paper-trade`** (zero risk):
   escalation policy, message contract, command protocol incl. owner-auth, fail-safe timeouts.
2. Stand up **ht-live** + `#live-trade` (read+write, scoped).
3. Enable live control ONLY once Phase 10 is armed (live execution integrated, kill-switch verified
   live, capital cap enforced).
4. Gradual scale-up with the escalation policy gating every step.

## 9. Test plan
- Pure `escalation_for` unit tests across every trigger + the fail-safe (missing input → ESCALATION).
- Command parser/auth: owner-only, explicit-verb-only, replay/expiry of request ids, STOP-ALL
  priority + never-gated.
- Timeout → safe-default for each escalation type.
- Loosening ceremony: ceiling never exceeded, two-step confirm required, auto-revert fires.
- Full audit/replay round-trip of an escalation→approval→effect timeline.
- All offline (injected reader/poster/clock); default-OFF so the suite stays network-free.

## 10. Open decisions for the human
- **Novelty thresholds** (§2.3): the k·stdev band, confidence bands, and the size %. Start
  conservative (escalate more), relax with data.
- **Owner user id(s)** for command auth (§4).
- **Posture presets** (§5): the concrete conservative/normal/aggressive parameter sets and the hard
  ceiling.
- **Digest cadence** for routine `#paper-trade` messages (§3).
- Whether `#live-trade` approvals should be **buttons** (Discord Interactions, needs the app Public
  Key + an HTTP endpoint) or **text replies** (polling, simpler) for v1.
