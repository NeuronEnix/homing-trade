"""Candidate-strategy intake (Phase 6 #7).

A periodic research scan that proposes NEW trading-algorithm ideas and FILES them as human-gated
`proposals(kind='strategy_toggle')` rows. It NEVER enables anything: a candidate becomes a
'pending' proposal a human must approve — and even on approve, `ProposalApplier` deliberately
refuses to auto-enable a strategy_toggle (wiring a runtime strategy registry is later work). So the
worst case is an extra pending suggestion in the queue.

Safety + robustness:
  * idempotent — a candidate whose name already has a pending strategy_toggle proposal is skipped,
    so repeated scans don't pile up duplicates (also deduped within a single batch);
  * the protected-fields guard in `create_proposal` still applies (a candidate can never smuggle a
    risk/secret/live payload — those keys raise and the candidate is skipped);
  * injectable `research_fn` (LLM-backed in production); degrades to a no-op on no fn / error /
    empty / garbled output, never crashing the loop. Default-OFF (and unbilled) until enabled.
"""
import json
import time

_SYSTEM = (
    "You are a quant researcher proposing CANDIDATE trading strategies for a BTC/ETH crypto-futures "
    "bot. Suggest concrete, well-known, backtestable algorithmic strategies (e.g. Supertrend trend-"
    "following, volume-confirmed breakout, TTM squeeze, z-score mean-reversion, regime filter). For "
    "each give a short snake_case `name`, a one-line `description` of the entry/exit logic, and a "
    "`rationale` for why it might add edge or diversification to the current set. Do NOT propose "
    "anything touching leverage, risk limits, position sizing, the kill-switch, or live-arming — "
    "those are off-limits. Respond ONLY with JSON: "
    '{"candidates": [{"name": "...", "description": "...", "rationale": "..."}]}'
)


class StrategyResearcher:
    """Files new candidate strategies as human-gated strategy_toggle proposals. Never enables."""

    def __init__(self, repo, research_fn=None, *, max_candidates=3, model="research"):
        self.repo = repo
        self.research_fn = research_fn
        self.max_candidates = max_candidates
        self.model = model

    def run_once(self, now_ms, *, existing_strategies=None):
        """Scan for candidate strategies and file NEW ones as strategy_toggle proposals. Returns the
        list of filed proposal ids (possibly empty). No-op without a research_fn; never raises."""
        if not self.research_fn:
            return []
        try:
            raw = self.research_fn(self._build_prompt(existing_strategies))
        except Exception:
            return []
        candidates = self._parse(raw)
        seen = self._pending_candidate_names()
        filed = []
        for c in candidates[:self.max_candidates]:
            name = c.get("name") if isinstance(c, dict) else None
            if not isinstance(name, str) or not name.strip():
                continue
            key = name.strip().lower()
            if key in seen:
                continue                       # already proposed (idempotent) / dup within batch
            payload = {"strategy": name.strip(), "action": "enable",
                       "description": str(c.get("description", ""))[:500]}
            rationale = str(c.get("rationale", "")).strip() or "candidate strategy from research scan"
            rationale = f"{rationale} [source: research/{self.model}]"   # audit: which model proposed it
            try:
                pid = self.repo.create_proposal(None, "strategy_toggle", payload, rationale, now_ms)
            except Exception:
                continue                       # protected-fields guard / DB error -> skip candidate
            seen.add(key)
            filed.append(pid)
        return filed

    def _pending_candidate_names(self):
        """Lower-cased strategy names already sitting in PENDING strategy_toggle proposals."""
        names = set()
        try:
            pending = self.repo.pending_proposals()
        except Exception:
            return names
        for p in pending:
            if p.get("kind") != "strategy_toggle":
                continue
            try:
                payload = json.loads(p["payload_json"])
            except Exception:
                continue
            n = (payload or {}).get("strategy") if isinstance(payload, dict) else None
            if isinstance(n, str) and n.strip():
                names.add(n.strip().lower())
        return names

    def _build_prompt(self, existing_strategies):
        have = sorted(existing_strategies) if existing_strategies else []
        return (_SYSTEM + "\n\nThe bot already runs these strategies (propose DIFFERENT ideas): "
                + (", ".join(have) if have else "(none yet)"))

    @staticmethod
    def _parse(raw):
        """Pull the candidate list out of the model's reply. [] on anything malformed."""
        if not isinstance(raw, str) or "{" not in raw:
            return []
        try:
            s, e = raw.find("{"), raw.rfind("}")
            data = json.loads(raw[s:e + 1])
        except Exception:
            return []
        cands = data.get("candidates") if isinstance(data, dict) else None
        return cands if isinstance(cands, list) else []


class ResearchRunner:
    """Runs the candidate-strategy scan on a slow wall-clock cadence (default daily), gated like the
    reflection loop: a no-op without a research_fn, and never lets a failure crash the engine."""

    def __init__(self, repo, research_fn=None, *, poll_sec=86400, max_candidates=3,
                 model="research", clock=None):
        self.researcher = StrategyResearcher(repo, research_fn, max_candidates=max_candidates,
                                             model=model)
        self.enabled = research_fn is not None
        self.poll_sec = poll_sec
        self._clock = clock or time.time
        self._last = None

    def run(self, existing_strategies=None):
        """File candidates if the cadence is due. Returns filed proposal ids (possibly empty)."""
        if not self.enabled:
            return []
        now = self._clock()
        if self._last is not None and (now - self._last) < self.poll_sec:
            return []
        self._last = now                       # stamp before the call so a slow/failed scan still
        try:                                   # spaces out the next attempt by poll_sec
            return self.researcher.run_once(int(now * 1000), existing_strategies=existing_strategies)
        except Exception:
            return []


def build_research_fn(cfg):
    """A `callable(prompt) -> raw_text` for the researcher, or None when research is disabled (so
    the runner no-ops and nothing is billed). Shares the reflection LLM backend config."""
    if not getattr(cfg, "research_enabled", False):
        return None
    from homing_trade.llm_text import text_completion_fn
    return text_completion_fn(
        getattr(cfg, "reflection_backend", "cli"),
        getattr(cfg, "research_model", "") or cfg.llm_model,
        timeout=getattr(cfg, "reflection_cli_timeout", 180),
        max_tokens=getattr(cfg, "reflection_max_tokens", 800))
