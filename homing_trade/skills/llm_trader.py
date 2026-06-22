"""LLM-driven multi-timeframe trading strategy.

Claude reads the 1-minute AND 15-minute chart state and decides WHEN to trade
(LONG / SHORT / CLOSE / HOLD) — direction and timing only. Position size, leverage,
and the daily risk limits are decided elsewhere (engine + DailyRiskGuard).

It calls the Anthropic API per decision (needs ANTHROPIC_API_KEY; costs money), so it
consults Claude only every `interval_sec` seconds and HOLDs in between. With no key,
no `anthropic` package, or any error, it degrades to HOLD — it never crashes the bot.
"""
import hashlib
import json
import time
from homing_trade import feed
from homing_trade import llm_backends
from homing_trade.llm_backends import _extract_json  # re-exported for back-compat
from homing_trade.skills.base import Strategy
from homing_trade.skills.indicators import ema, rsi
from homing_trade.models import Candle, Signal

DEFAULT_TIMEFRAMES = ("15m", "1h", "4h")   # bird's-eye context; AI drills down on request

# Identity of THIS system prompt. Bump on any change to _SYSTEM/_SCHEMA so a decision is
# attributable to the exact prompt that produced it. When an approved playbook is injected, the
# effective prompt_version becomes f"{PROMPT_VERSION}+{playbook_version}".
PROMPT_VERSION = "mtf-v2"

_SCHEMA = {
    "type": "object",
    "properties": {
        "observation": {"type": "string"},   # what you SEE across the charts
        "prediction": {"type": "string"},     # what you PREDICT price will do next
        "rationale": {"type": "string"},      # WHY that prediction leads to this decision
        "action": {"type": "string", "enum": ["LONG", "SHORT", "CLOSE", "HOLD"]},
        "confidence": {"type": "number"},
        "next_check_in_sec": {"type": "number"},  # how soon you want to look again
        "requested_charts": {                  # OPTIONAL: which charts you want to see NEXT time
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "interval": {"type": "string"},
                    "limit": {"type": "number"},
                    "start": {"type": "string"},   # ISO-8601 UTC, optional date range
                    "end": {"type": "string"},
                },
                "required": ["interval"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["observation", "prediction", "rationale", "action", "confidence", "next_check_in_sec"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You are a disciplined crypto-futures trader for the BTC/USDT perpetual (INR margin; "
    "leverage and position size are handled elsewhere — decide direction and timing only). "
    "Work TOP-DOWN: the charts under 'charts' are higher timeframes for a bird's-eye view "
    "(context/trend). Use them to judge whether a setup exists; when one does, NARROW DOWN by "
    "requesting lower timeframes (e.g. 5m, 1m) via requested_charts to time the entry — and "
    "shorten next_check_in_sec so you watch closely while narrowing. Default to HOLD unless "
    "there is a clear, multi-timeframe edge: trend alignment across timeframes, a momentum "
    "extreme to fade, or a clean breakout with expanding volatility. Avoid choppy, low-volatility, "
    "or conflicting tapes — they bleed fees, especially at high leverage. LONG only when flat and "
    "bullish; SHORT only when flat and bearish; CLOSE to exit an open position when the thesis is gone.\n\n"
    "If the data includes a 'playbook' field, those are hard-won rules distilled from YOUR OWN "
    "past trades on this instrument (human-approved). Treat them as priors: follow them unless "
    "the current setup clearly contradicts one, and weigh them in your rationale.\n\n"
    "If the data includes a 'fear_greed' field (the crypto Fear & Greed index: value 0-100 + "
    "classification), use it as a CONTEXTUAL sentiment gauge — extremes often mark exhaustion "
    "(extreme greed -> caution on fresh longs; extreme fear -> caution on fresh shorts) — never as "
    "a standalone trigger; the price action across timeframes still leads.\n\n"
    "Respond ONLY with the JSON schema, and be concrete:\n"
    "  observation — what you actually SEE across the charts (trend, EMAs, RSI, volatility).\n"
    "  prediction  — what you PREDICT price will do next, and over what horizon.\n"
    "  rationale   — WHY that prediction leads to this action (tie observation -> prediction -> decision).\n"
    "  action, confidence (0-1).\n"
    "  next_check_in_sec — seconds until you want to look again; at most 'max_check_sec' (given in "
    "the data), but request SOONER (down to ~60) when a setup is developing or you hold a position.\n"
    "  requested_charts — OPTIONAL: choose which charts you see NEXT time. Each item: interval (one of "
    "'available_intervals'), optional limit (<=1000 candles), and an optional UTC date range "
    "start/end as ISO-8601 strings (e.g. '2026-06-20T00:00:00Z'). Omit to keep the default set."
)


def _tf_summary(closes, candles):
    e9, e21, r = ema(closes, 9), ema(closes, 21), rsi(closes, 14)
    w = candles[-20:]
    ref = (sum(c.close for c in w) / len(w)) if w else closes[-1]
    vol = (max(c.high for c in w) - min(c.low for c in w)) / ref * 100 if (w and ref) else 0.0
    trend = "up" if (e9 and e21 and e9 > e21) else "down" if (e9 and e21 and e9 < e21) else "flat"
    return {
        "last": round(closes[-1], 2),
        "ema9": round(e9, 1) if e9 else None,
        "ema21": round(e21, 1) if e21 else None,
        "rsi14": round(r, 1) if r else None,
        "trend": trend,
        "vol20_pct": round(vol, 2),
        "recent": [round(c, 1) for c in closes[-10:]],
    }


def resample(candles, factor):
    """Aggregate 1m candles into `factor`-minute OHLC candles (oldest-first preserved)."""
    out = []
    for i in range(0, len(candles), factor):
        bucket = candles[i:i + factor]
        if not bucket:
            continue
        out.append(Candle(open=bucket[0].open, high=max(c.high for c in bucket),
                          low=min(c.low for c in bucket), close=bucket[-1].close,
                          volume=sum(c.volume for c in bucket), time=bucket[0].time))
    return out


class LlmTrader(Strategy):
    name = "llm_trader"

    def __init__(self, model="claude-opus-4-8", interval_sec=900, client=None,
                 max_tokens=600, backend="api", cli_timeout=120,
                 pair="B-BTC_USDT", provider=None, name=None, clock=None, min_interval_sec=60,
                 timeframes=DEFAULT_TIMEFRAMES, chart_limit=150,
                 playbook_provider=None, playbook_max_rules=12, fng_provider=None):
        self.name = name or "llm_trader"   # per-instance so multiple brains get separate wallets
        self.model = model
        self.interval_sec = interval_sec        # the configured MAX gap between consults (seconds)
        self.min_interval_sec = min_interval_sec  # floor the AI can shorten to (watch closely)
        self._client = client
        self.max_tokens = max_tokens
        self.backend = backend          # one of llm_backends.BACKENDS (cli/api/openai/mistral/llama)
        self.cli_timeout = cli_timeout
        self.pair = pair
        self.timeframes = tuple(timeframes)     # default charts shown each consult
        self.chart_limit = chart_limit          # candles per chart
        self._provider = provider       # callable(interval,limit,start,end)->[Candle]; defaults to feed
        self._requested = None           # charts the AI asked to see next (overrides defaults)
        self._clock = clock or time.time  # WALL-CLOCK cadence — decoupled from the candle loop
        self._last_decision_ts = None
        self._next_interval_sec = interval_sec  # AI sets this each consult (<= interval_sec)
        # callable() -> the current published playbook row ({"version", "rules_json"}) or None.
        # Wired by SkillRunner to read the ledger; None in unit tests / when no playbook exists.
        self._playbook_provider = playbook_provider
        self.playbook_max_rules = playbook_max_rules
        # callable() -> the current external sentiment reading (e.g. Fear & Greed dict) or None.
        # Wired by SkillRunner to the cached signal; None in unit tests / when ingestion is off.
        self._fng_provider = fng_provider

    def set_playbook_provider(self, provider):
        """Inject (post-construction) the source of this trader's current playbook — SkillRunner
        wires it to the ledger since build_ai_traders has no ledger reference."""
        self._playbook_provider = provider

    def set_fng_provider(self, provider):
        """Inject (post-construction) the source of the current Fear & Greed reading — SkillRunner
        wires it to the cached signal; build_ai_traders has no ledger reference."""
        self._fng_provider = provider

    def _current_fng(self):
        """The current sentiment reading to inject, or None. Never raises — a failed read just
        omits the field so the consult proceeds on price action alone."""
        if not self._fng_provider:
            return None
        try:
            return self._fng_provider() or None
        except Exception:
            return None

    def _current_playbook(self):
        """(version, [rule, ...]) for THIS strategy's current playbook — bounded to top-K, with
        blank/non-string rules dropped. Degrades to (None, []) on no provider, a read error, a
        malformed rules_json, or an empty rule set, so a failure just yields the base prompt and
        never crashes the consult. (None, []) also means: claim no playbook_version, since
        nothing was actually injected."""
        if not self._playbook_provider:
            return (None, [])
        try:
            pbk = self._playbook_provider()
            if not pbk:
                return (None, [])
            parsed = json.loads(pbk["rules_json"])
            # Only a dict-with-"rules" (the publish_playbook contract) or a bare list is valid.
            # A scalar (e.g. a JSON string) is NOT iterated — that would mint per-character
            # "rules" from a corrupted row; treat it as no rules instead.
            raw_rules = (parsed.get("rules", []) if isinstance(parsed, dict)
                         else parsed if isinstance(parsed, list) else [])
            rules = [r.strip() for r in raw_rules
                     if isinstance(r, str) and r.strip()][:self.playbook_max_rules]
            return (pbk.get("version"), rules) if rules else (None, [])
        except Exception:
            return (None, [])

    def _decide(self, user):
        """Make one decision via the configured backend adapter. Returns
        (decision_dict, raw_text, usage_dict); raises on any provider/SDK/network error
        (on_candle catches -> HOLD)."""
        req = llm_backends.BackendRequest(
            prompt=user, system=_SYSTEM, model=self.model, max_tokens=self.max_tokens,
            schema=_SCHEMA, client=self._client, cli_timeout=self.cli_timeout)
        return llm_backends.decide(self.backend, req)

    def _provide(self, interval, limit=150, start=None, end=None):
        if self._provider is not None:
            return self._provider(interval, limit, start, end)
        return feed.get_candles(self.pair, interval, limit=limit, start=start, end=end)

    def _specs(self):
        """The charts to fetch this consult: the AI's request if it made one, else defaults."""
        if self._requested:
            return self._requested
        return [{"interval": iv, "limit": self.chart_limit} for iv in self.timeframes]

    def _validate_charts(self, req):
        """Sanitize the AI's requested_charts: valid interval, limit in [1,1000], parseable
        ISO dates. Returns a clean list or None (fall back to defaults)."""
        if not isinstance(req, list) or not req:
            return None
        valid = []
        for spec in req:
            if not isinstance(spec, dict) or spec.get("interval") not in feed.INTERVALS:
                continue
            try:
                feed.to_ms(spec.get("start"))
                feed.to_ms(spec.get("end"))
            except Exception:
                continue  # unparseable date range -> drop this spec
            valid.append({"interval": spec["interval"],
                          "limit": max(1, min(int(spec.get("limit") or self.chart_limit), 1000)),
                          "start": spec.get("start") or None,
                          "end": spec.get("end") or None})
        return valid or None

    def _build_context(self, candles, position, playbook_rules=()):
        charts = {}
        for spec in self._specs():
            iv = spec.get("interval")
            if iv not in feed.INTERVALS:
                continue
            lim = max(1, min(int(spec.get("limit") or self.chart_limit), 1000))
            start, end = spec.get("start"), spec.get("end")
            try:
                c = self._provide(iv, lim, start, end)
            except Exception as exc:
                charts[iv] = {"error": str(exc)[:120]}
                continue
            label = iv if not (start or end) else f"{iv} {start or ''}..{end or ''}".strip()
            charts[label] = (_tf_summary([x.close for x in c], c) if len(c) >= 21
                             else {"n": len(c), "note": "insufficient data"})
        ctx = {
            "charts": charts,
            "position": (position.side if position else "flat"),
            "max_check_sec": self.interval_sec,           # ceiling for next_check_in_sec
            "available_intervals": list(feed.INTERVALS),  # what you may request
        }
        if playbook_rules:
            ctx["playbook"] = list(playbook_rules)         # learned, human-approved priors
        fng = self._current_fng()
        if fng:
            ctx["fear_greed"] = fng                        # external sentiment context (Phase 6)
        return ctx

    def on_candle(self, candles, position):
        # Cadence: consult Claude only every interval_sec seconds of WALL-CLOCK time, so the
        # brain can poll faster (or slower) than the engine's candle interval. HOLD in between.
        now = self._clock()
        if self._last_decision_ts is not None and (now - self._last_decision_ts) < self._next_interval_sec:
            return Signal("HOLD", reason=f"waiting (next LLM check in ~{self._next_interval_sec:g}s)")
        pb_version, pb_rules = self._current_playbook()
        ctx = self._build_context(candles, position, pb_rules)
        user = "Decide the trade. Charts:\n" + json.dumps(ctx)
        # Provenance for replay/attribution: the prompt identity (base ⊕ playbook version) and a
        # LOGICAL fingerprint of (system, user-context). It's a stable identity for "same prompt
        # → same hash", NOT the exact wire bytes (the API sends system/content separately; the
        # CLI appends a trailing instruction). Recorded by process_tick on the decision + response.
        prompt_version = PROMPT_VERSION if not pb_version else f"{PROMPT_VERSION}+{pb_version}"
        prompt_hash = hashlib.sha256((_SYSTEM + "\n" + user).encode("utf-8")).hexdigest()[:16]
        meta_prov = {"prompt_version": prompt_version, "playbook_version": pb_version,
                     "prompt_hash": prompt_hash}
        try:
            data, raw, usage = self._decide(user)
            self._last_decision_ts = now
            action = str(data["action"]).upper()
            # guard the mapping so the engine never gets an impossible action
            if action == "LONG" and position is not None:
                action = "HOLD"
            if action == "CLOSE" and position is None:
                action = "HOLD"
            if action not in ("LONG", "SHORT", "CLOSE", "HOLD"):
                action = "HOLD"
            obs = str(data.get("observation", ""))
            pred = str(data.get("prediction", ""))
            rat = str(data.get("rationale", data.get("reason", "")))  # back-compat with older payloads
            # AI paces itself: it may shorten the next gap, capped at the configured max.
            try:
                req = float(data.get("next_check_in_sec", self.interval_sec))
            except (TypeError, ValueError):
                req = self.interval_sec
            self._next_interval_sec = max(self.min_interval_sec, min(req, self.interval_sec))
            # AI picks the charts it wants next time (validated); else fall back to defaults.
            self._requested = self._validate_charts(data.get("requested_charts"))
            reason = f"LLM({self.backend}) {action}: {rat}"
            if pred:
                reason += f" | predicts: {pred}"
            reason += f" [recheck ~{self._next_interval_sec:g}s]"
            # per-timeframe trend, derived from the charts we showed
            trends = {lbl: c["trend"] for lbl, c in ctx["charts"].items()
                      if isinstance(c, dict) and "trend" in c}
            return Signal(
                action,
                confidence=float(data.get("confidence", 0.5)),
                reason=reason[:400],
                indicators=trends,
                raw=raw,
                meta={"observation": obs, "prediction": pred, "rationale": rat,
                      "next_check_in_sec": self._next_interval_sec,
                      "requested_charts": self._requested, "usage": usage, **meta_prov},
            )
        except Exception as exc:  # missing key/package/CLI, network, bad JSON -> HOLD + error alert
            # Carry the provenance even on failure, so a failing consult's error row is still
            # attributable to the exact prompt/playbook that produced it.
            return Signal("HOLD", reason=f"llm unavailable: {exc}", error=str(exc),
                          meta=meta_prov)
