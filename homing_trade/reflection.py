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
        # 1. only reflect over outcomes realized since the last reflection (watermark) AND
        #    already realized as of now (look-ahead embargo).
        last = self.repo.recent_reflections(strategy, 1)
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
        lesson = (parsed.get("lesson") or "").strip()
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
