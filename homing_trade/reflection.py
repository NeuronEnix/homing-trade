"""The primary learn->correct loop: batched retrospection over completed trades.

`ReflectionEngine.run_once(strategy, now_ms)` looks at the trade_outcomes realized since the
last reflection (embargo-enforced via `as_of=now_ms`), summarizes them with the mechanical
attribution from `SelfQuery` (win rate / per-regime / per-exit / directional accuracy — all
computed from prices, never the model's self-grade), asks an injected LLM for a compact lesson
plus a proposed set of playbook rules, then:

  1. records a `reflection` row (the lesson + the batch + metrics), and
  2. files a `proposal` (kind='playbook') carrying the proposed rules.

It NEVER publishes a playbook or applies anything — the proposal is human-gated (and the
protected-fields guard in `create_proposal` still applies). It is wall-clock-paced and
decoupled from the candle loop: the caller decides cadence; this just does one pass. It never
crashes the loop — a missing/erroring/garbled model simply yields no reflection (returns None).

`reflect_on_trade(strategy, position_id, now_ms)` is the SECONDARY loop: one focused critique
of a single completed trade at CLOSE. It joins the closed outcome to its originating decision
(via decision_id) and the AI's observation/prediction/rationale, presents the MECHANICAL
outcome (realized P&L, prediction_correct, MAE/MFE, exit_reason — all from prices/the ledger,
never the model's self-grade), and records a `per_trade` reflection. It files NO proposal — the
periodic loop owns playbook proposals; per-trade is a critique stream that informs it. Same
never-crash / no-op-without-a-model contract.
"""
import json

from homing_trade.selfquery import SelfQuery


def _brace_slice(s):
    """The outermost {...} slice of a string, or None — for prose/markdown-wrapped JSON."""
    start, end = s.find("{"), s.rfind("}")
    return s[start:end + 1] if (start != -1 and end > start) else None


class ReflectionEngine:
    def __init__(self, repo, reflect_fn=None, *, starting_balance=5000.0, min_trades=5,
                 model="reflection"):
        self.repo = repo
        self.reflect_fn = reflect_fn          # callable(prompt:str) -> raw model text
        self.sq = SelfQuery(repo, starting_balance)
        self.min_trades = min_trades
        self.model = model

    def run_once(self, strategy, now_ms):
        """One retrospection pass. Returns a dict summary, or None when skipped (no model, too
        few new outcomes, or an unusable model response)."""
        if self.reflect_fn is None:
            return None
        # 1. only reflect over outcomes realized since the last PERIODIC reflection (watermark)
        #    AND already realized as of now (look-ahead embargo). kind='periodic' so a per_trade
        #    reflection (secondary loop, more recent in ts but covering one old trade) can't be
        #    mistaken for the watermark and make us re-reflect old outcomes.
        last = self.repo.recent_reflections(strategy, 1, kind="periodic")
        since = last[0]["batch_to_ts"] if (last and last[0]["batch_to_ts"] is not None) else 0
        outcomes = [o for o in self.repo.trade_outcomes(strategy, as_of=now_ms)
                    if (o.get("realized_at_ts") or 0) > since]
        if len(outcomes) < self.min_trades:
            return None                        # not enough fresh evidence
        # 2. mechanical attribution (read-only; prediction_correct comes from prices)
        metrics = {
            "performance": self.sq.performance(strategy),
            "by_regime": self.sq.regime_performance(strategy, as_of=now_ms),
            "by_exit_reason": self.sq.exit_reason_breakdown(strategy, as_of=now_ms),
            "directional_accuracy": self.sq.directional_accuracy(strategy, as_of=now_ms),
        }
        current = self.repo.latest_playbook(strategy)
        # 3. consult the model — never let its failure crash the loop
        try:
            raw = self.reflect_fn(self._build_prompt(strategy, outcomes, metrics, current))
            parsed = self._parse(raw)
        except Exception:
            return None
        if not parsed:
            return None
        lesson = self._lesson(parsed)
        rules = parsed.get("rules") or []
        if not isinstance(rules, list):
            rules = []
        # Rules are playbook TEXT — keep only strings. This also makes it structurally
        # impossible for a model to smuggle a protected field as a rule object (e.g.
        # {"leverage": 3}), which would otherwise trip the create_proposal guard.
        rules = [r for r in rules if isinstance(r, str) and r.strip()]
        new_version = f"{strategy}-{now_ms}" if rules else None
        trade_ids = [o["position_id"] for o in outcomes]
        # 4. persist the reflection
        rid = self.repo.record_reflection(
            strategy, "periodic", now_ms, batch_from_ts=since, batch_to_ts=now_ms,
            trade_ids=trade_ids, metrics=metrics, lesson=lesson,
            new_playbook_version=new_version, model=self.model, raw=raw)
        # 5. file the proposal (human-gated; NOT published here). The protected-fields guard
        #    still applies in create_proposal.
        proposal_id = None
        if rules:
            proposal_id = self.repo.create_proposal(
                strategy, "playbook",
                {"version": new_version, "rules": rules,
                 "parent_version": current["version"] if current else None},
                lesson or "reflection-proposed playbook update", now_ms,
                source_reflection_id=rid)
        return {"reflection_id": rid, "proposal_id": proposal_id,
                "new_version": new_version, "n_trades": len(outcomes)}

    def reflect_on_trade(self, strategy, position_id, now_ms):
        """One per-trade critique (secondary loop). Returns a dict summary, or None when skipped
        (no model, embargoed/missing outcome, model error, garbled/empty response)."""
        if self.reflect_fn is None:
            return None
        # embargo: only critique a trade already realized as of now (no peeking at the future).
        outcome = next((o for o in self.repo.trade_outcomes(strategy, as_of=now_ms)
                        if o.get("position_id") == position_id), None)
        if outcome is None:
            return None
        # idempotent: ONE critique per trade. Bail before consulting the model so a re-fire
        # (retry, restart-replay of recent closes, manual+auto close of the same position)
        # neither double-spends the LLM nor double-writes — the "one pass at close" contract
        # is enforced here, not just assumed of the caller.
        if self.repo.per_trade_reflection_exists(strategy, position_id):
            return None
        # trace back to the entry thesis (decision + AI observation/prediction/rationale).
        decision = self.repo.get_decision(outcome.get("decision_id"))
        llm = self.repo.llm_response_at(strategy, decision["ts"]) if decision else None
        try:
            raw = self.reflect_fn(self._build_trade_prompt(strategy, outcome, decision, llm))
            parsed = self._parse(raw)
        except Exception:
            return None
        if not parsed:
            return None
        lesson = self._lesson(parsed)
        if not lesson:
            return None                        # nothing concrete learned -> record nothing
        metrics = {k: outcome.get(k) for k in (
            "side", "entry_price", "exit_price", "realized_pnl", "pnl_pct",
            "prediction_correct", "mae", "mfe", "exit_reason", "holding_period_ms",
            "regime_at_entry")}
        rid = self.repo.record_reflection(
            strategy, "per_trade", now_ms,
            batch_from_ts=outcome.get("entry_ts"), batch_to_ts=outcome.get("exit_ts"),
            trade_ids=[position_id], metrics=metrics, lesson=lesson,
            new_playbook_version=None, model=self.model, raw=raw)
        return {"reflection_id": rid, "position_id": position_id,
                "prediction_correct": outcome.get("prediction_correct")}

    def _build_trade_prompt(self, strategy, outcome, decision, llm):
        d, l = decision or {}, llm or {}
        thesis = {
            "intended_action": d.get("intended_action") or d.get("action"),
            "confidence": d.get("confidence"),
            "decision_reason": d.get("reason"),         # the indicator/mechanical reason
            "observation": l.get("observation"),         # the AI's free-text thesis (if any)
            "prediction": l.get("prediction"),
            "rationale": l.get("rationale"),
        }
        facts = {k: outcome.get(k) for k in (
            "side", "entry_price", "exit_price", "realized_pnl", "pnl_pct",
            "prediction_correct", "mae", "mfe", "exit_reason", "holding_period_ms",
            "regime_at_entry")}
        return (
            "You are critiquing ONE completed trade to extract a single concrete lesson.\n\n"
            f"Strategy: {strategy}\n\n"
            "What the strategy SAID at entry — its thesis. This is opinion and may have been "
            "wrong:\n"
            f"{json.dumps(thesis, indent=2, default=str)}\n\n"
            "What ACTUALLY happened — MECHANICAL ground truth computed from prices/the ledger. "
            "prediction_correct is scored from the price path, NOT your opinion; do NOT re-grade "
            "it:\n"
            f"{json.dumps(facts, indent=2, default=str)}\n\n"
            "Critique the entry thesis against the outcome: was the reasoning sound given what "
            "the market did, or did it ignore something visible at the time? Return STRICT JSON "
            "only: {\"lesson\": \"<one concrete, specific, actionable lesson>\"}. If the trade "
            "is unremarkable with nothing to learn, return {\"lesson\": \"\"}."
        )

    def _build_prompt(self, strategy, outcomes, metrics, current):
        rules = []
        if current:
            try:
                rules = json.loads(current["rules_json"]).get("rules", [])
            except Exception:
                rules = []
        return (
            "You are reviewing the realized track record of a trading strategy to extract one "
            "concrete lesson and propose refined playbook rules.\n\n"
            f"Strategy: {strategy}\n"
            f"Completed trades this batch: {len(outcomes)}\n"
            "MECHANICAL metrics (computed from prices — these are ground truth, not your "
            "opinion; do NOT re-grade whether predictions were correct):\n"
            f"{json.dumps(metrics, indent=2, default=str)}\n\n"
            f"Current playbook rules: {json.dumps(rules)}\n\n"
            "Return STRICT JSON only: {\"lesson\": \"<one concrete lesson>\", "
            "\"rules\": [\"<refined rule>\", ...]}. Refine/supersede stale rules rather than "
            "blindly appending. If there is no improvement to make, return an empty rules list."
        )

    @staticmethod
    def _lesson(parsed):
        """A model 'lesson' coerced to a clean string ('' if absent or not text). A non-string
        lesson (e.g. a list) must never reach .strip() and crash the loop."""
        v = parsed.get("lesson")
        return v.strip() if isinstance(v, str) else ""

    @staticmethod
    def _parse(raw):
        """Pull the JSON OBJECT out of a possibly-prose/markdown-wrapped model response. Returns
        a dict or None — a valid-but-non-object response (bare list/number/string) yields None,
        so the caller never does .get() on a non-dict (that path used to crash the loop)."""
        if not raw:
            return None
        for candidate in (raw, _brace_slice(raw)):
            if candidate is None:
                continue
            try:
                obj = json.loads(candidate)
            except Exception:
                continue
            if isinstance(obj, dict):
                return obj
        return None
