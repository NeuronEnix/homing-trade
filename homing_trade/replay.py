"""Backlog: deterministic decision/candle replay for trade-by-trade audit.

Reconstructs, decision-by-decision and in chronological order, the full story the bot recorded —
purely from the audit-truth tables, joined by the keys the engine wrote them with. Same DB in →
same replay out: no network, no randomness, no model calls. This is the human's trade-by-trade
audit lens over `decision_log` → `llm_responses` (the AI thesis) → `trades` (the fills) →
`trade_outcomes` (the realized result), plus any `risk_events` that vetoed the tick.

Join semantics — exactly how the engine wrote the rows (no fuzzy matching, nothing fabricated):
  - llm thesis ↔ decision: same strategy + same wall-clock tick `ts` (db.llm_response_at relies on
    this exact-ts match); the highest-id row wins if a tick logged more than one. None for a
    mechanical (non-AI) skill.
  - trades / outcome ↔ decision: shared `decision_id` (the engine stamps the triggering decision
    onto the OPEN trade and the denormalized outcome). A decision with no decision_id (older rows,
    or a HOLD that opened nothing) simply has no linked fills — shown as-is.
  - risk veto ↔ decision: same strategy + same tick `ts`.

The pure core `correlate(...)` takes plain row dicts and is fully unit-tested offline; `build_replay`
is the thin reader that pulls those rows through the repository (consumers never issue raw SQL).
"""
from dataclasses import dataclass

from homing_trade.profit_mirage import cutoff_ms_from_iso


@dataclass(frozen=True)
class ReplayStep:
    """One decision with everything the ledger recorded around that tick."""
    decision: dict
    llm: dict = None
    trades: tuple = ()
    outcome: dict = None
    risk_events: tuple = ()

    @property
    def ts(self):
        return self.decision.get("ts")

    @property
    def strategy(self):
        return self.decision.get("strategy")

    @property
    def decision_id(self):
        return self.decision.get("decision_id")

    @property
    def was_blocked(self):
        """A tick a risk guard VETOED, or whose taken action the engine explicitly recorded as
        BLOCKED/PAUSED — the rows an auditor most wants to find. NOTE: a LONG/SHORT signal that the
        engine simply ignored because a position was already open is NOT a block (it writes
        taken=HOLD with no veto) — so we never infer a block merely from taken != intended."""
        taken = (self.decision.get("taken_action") or "").upper()
        return bool(self.risk_events) or taken in ("BLOCKED", "PAUSED")


def _highest_id(a, b):
    return a if (a.get("id") or 0) >= (b.get("id") or 0) else b


def _risk_only_step(event):
    """Wrap a risk_event that matched no decision (e.g. the kill-switch `halt`, which the engine
    writes with strategy=None and its own post-tick ts) as a standalone step, so it is NEVER
    silently dropped from the audit."""
    synthetic = {"strategy": event.get("strategy"), "ts": event.get("ts"),
                 "taken_action": "RISK-EVENT", "reason": None}
    return ReplayStep(decision=synthetic, risk_events=(event,))


def correlate(decisions, llm_responses=(), trades=(), outcomes=(), risk_events=(), *, strategy=None):
    """Join the audit rows into chronological ReplayStep records. Pure + deterministic: ordering is
    by (ts, kind, id), and every match uses an exact key the engine wrote — never a heuristic.
    Risk events that match no decision are emitted as their own steps (scoped to `strategy` when
    given: a global halt has strategy=None and is always kept) so nothing is lost."""
    llm_idx = {}
    for r in llm_responses:
        key = (r.get("strategy"), r.get("ts"))
        llm_idx[key] = _highest_id(llm_idx[key], r) if key in llm_idx else r

    # An OPEN trade carries (decision_id, position_id); the matching CLOSE carries the same
    # position_id but no decision_id. Map position_id → decision_id so CLOSE fills attach to the
    # decision that opened them — otherwise the audit would show entries but never exits.
    pos_to_did = {}
    for t in trades:
        did, pid = t.get("decision_id"), t.get("position_id")
        if did and pid is not None:
            pos_to_did.setdefault(pid, did)
    trades_by_did = {}
    for t in trades:
        did = t.get("decision_id") or pos_to_did.get(t.get("position_id"))
        if did:
            trades_by_did.setdefault(did, []).append(t)

    outcome_by_did = {}
    for o in outcomes:
        did = o.get("decision_id")
        if did and did not in outcome_by_did:   # decision_id is unique per outcome; first wins
            outcome_by_did[did] = o

    risk_idx, consumed = {}, set()
    for e in risk_events:
        risk_idx.setdefault((e.get("strategy"), e.get("ts")), []).append(e)

    steps = []   # (sort_key, step)
    for d in decisions:
        did = d.get("decision_id")
        key = (d.get("strategy"), d.get("ts"))
        matched = risk_idx.get(key, [])
        consumed.add(key)
        linked = trades_by_did.get(did, []) if did else []
        steps.append(((d.get("ts") or 0, 0, d.get("id") or 0), ReplayStep(
            decision=d,
            llm=llm_idx.get(key),
            trades=tuple(sorted(linked, key=lambda t: (t.get("ts") or 0, t.get("id") or 0))),
            outcome=outcome_by_did.get(did) if did else None,
            risk_events=tuple(matched),
        )))

    for (s, ts), events in risk_idx.items():
        if (s, ts) in consumed:
            continue                                  # already attached to a decision
        if strategy is not None and s not in (None, strategy):
            continue                                  # another strategy's veto, out of scope
        for e in events:
            steps.append(((ts or 0, 1, e.get("id") or 0), _risk_only_step(e)))

    steps.sort(key=lambda pair: pair[0])
    return [step for _, step in steps]


def build_replay(repo, *, strategy=None, since_iso=None, until_iso=None):
    """Read the audit rows for the window through the repository and correlate them. since/until are
    ISO-8601 UTC (blank/None = unbounded); an invalid non-blank value raises (fail-closed parse)."""
    start_ms = cutoff_ms_from_iso(since_iso)
    end_ms = cutoff_ms_from_iso(until_iso)
    decisions = repo.decisions_in_range(strategy, start_ms, end_ms)
    llm = repo.llm_responses_in_range(strategy, start_ms, end_ms)
    trades = repo.trades_in_range(strategy, start_ms, end_ms)
    # Fetch ALL risk events in the window (strategy=None), not just this strategy's: the kill-switch
    # `halt` is written with strategy=None and would be filtered out otherwise. correlate scopes the
    # unmatched ones back to `strategy` (keeping global/None halts) so the audit never loses a trip.
    risk = repo.risk_events_in_range(None, start_ms, end_ms)
    outcomes = repo.trade_outcomes(strategy)        # joined by decision_id, not by window
    return correlate(decisions, llm, trades, outcomes, risk, strategy=strategy)


# ── rendering ─────────────────────────────────────────────────────────────────────────────────
def _fmt_ms(ms):
    """Epoch-ms → ISO-8601 UTC for display. Deterministic; not wall-clock."""
    if ms is None:
        return "?"
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def render(steps, *, verbose=False):
    """A human-readable trade-by-trade audit. Deterministic given the steps."""
    out = []
    for i, s in enumerate(steps, 1):
        d = s.decision
        flag = " ⚠ BLOCKED/VETOED" if s.was_blocked else ""
        action = d.get("taken_action") or d.get("action") or "?"
        conf = d.get("confidence")
        conf_s = f" conf={conf:.2f}" if isinstance(conf, (int, float)) else ""
        out.append(f"[{i}] {_fmt_ms(s.ts)}  {s.strategy or '?'}  {action}{conf_s}"
                   f"  regime={d.get('regime') or '-'}{flag}")
        reason = d.get("reason") or d.get("rejection_rationale")
        if reason:
            out.append(f"      why: {reason}")
        if s.llm:
            thesis = s.llm.get("prediction") or s.llm.get("rationale") or s.llm.get("observation")
            if thesis:
                out.append(f"      AI[{s.llm.get('model') or s.llm.get('backend') or 'llm'}]: {thesis}")
        for e in s.risk_events:
            out.append(f"      VETO[{e.get('kind')}]: {e.get('reason')}")
        for t in s.trades:
            out.append(f"      fill: {t.get('action')} {t.get('side')} "
                       f"size={t.get('size')} @ {t.get('price')} (slip={t.get('slippage')})")
        if s.outcome:
            o = s.outcome
            out.append(f"      outcome: pnl={o.get('realized_pnl')} ({o.get('pnl_pct')}%) "
                       f"exit={o.get('exit_reason')} correct={o.get('prediction_correct')}")
        if verbose:
            out.append(f"      decision_id={s.decision_id} playbook={d.get('playbook_version') or '-'} "
                       f"prompt={d.get('prompt_version') or '-'}")
    return "\n".join(out)


def _main(argv=None):
    import argparse

    from homing_trade.repository import Repository
    p = argparse.ArgumentParser(description="Deterministic trade-by-trade decision replay/audit.")
    p.add_argument("--db", default="data/paper_trading.db", help="SQLite path")
    p.add_argument("--strategy", default=None, help="limit to one strategy")
    p.add_argument("--since", default=None, help="ISO-8601 UTC start (inclusive)")
    p.add_argument("--until", default=None, help="ISO-8601 UTC end (inclusive)")
    p.add_argument("--verbose", action="store_true", help="show provenance ids per step")
    args = p.parse_args(argv)

    repo = Repository.open(args.db)
    try:
        steps = build_replay(repo, strategy=args.strategy, since_iso=args.since, until_iso=args.until)
        print(render(steps, verbose=args.verbose))
        print(f"\n{len(steps)} decision(s); "
              f"{sum(1 for s in steps if s.was_blocked)} blocked/vetoed; "
              f"{sum(len(s.trades) for s in steps)} fill(s).")
    finally:
        repo.db.close()


if __name__ == "__main__":   # pragma: no cover
    _main()
