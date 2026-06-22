"""LLM-driven multi-timeframe trading strategy.

Claude reads the 1-minute AND 15-minute chart state and decides WHEN to trade
(LONG / SHORT / CLOSE / HOLD) — direction and timing only. Position size, leverage,
and the daily risk limits are decided elsewhere (engine + DailyRiskGuard).

It calls the Anthropic API per decision (needs ANTHROPIC_API_KEY; costs money), so it
consults Claude only every `interval_sec` seconds and HOLDs in between. With no key,
no `anthropic` package, or any error, it degrades to HOLD — it never crashes the bot.
"""
import json
import time
from homing_trade import feed
from homing_trade.skills.base import Strategy
from homing_trade.skills.indicators import ema, rsi
from homing_trade.models import Candle, Signal

DEFAULT_TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h")

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
    "You are given several timeframe charts under 'charts'. Default to HOLD unless there is a "
    "clear, multi-timeframe edge: trend alignment across timeframes, a momentum extreme to fade, "
    "or a clean breakout with expanding volatility. Avoid choppy, low-volatility, or conflicting "
    "tapes — they bleed fees, especially at high leverage. LONG only when flat and bullish; SHORT "
    "only when flat and bearish; CLOSE to exit an open position when the thesis is gone.\n\n"
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


def _extract_json(text):
    """Pull the first {...} JSON object out of an LLM's text reply."""
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1 or e < s:
        raise ValueError("no JSON object in LLM response")
    return json.loads(text[s:e + 1])


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
                 timeframes=DEFAULT_TIMEFRAMES, chart_limit=150):
        self.name = name or "llm_trader"   # per-instance so multiple brains get separate wallets
        self.model = model
        self.interval_sec = interval_sec        # the configured MAX gap between consults (seconds)
        self.min_interval_sec = min_interval_sec  # floor the AI can shorten to (watch closely)
        self._client = client
        self.max_tokens = max_tokens
        self.backend = backend          # "cli" (claude headless, no key) | "api" (anthropic SDK)
        self.cli_timeout = cli_timeout
        self.pair = pair
        self.timeframes = tuple(timeframes)     # default charts shown each consult
        self.chart_limit = chart_limit          # candles per chart
        self._provider = provider       # callable(interval,limit,start,end)->[Candle]; defaults to feed
        self._requested = None           # charts the AI asked to see next (overrides defaults)
        self._clock = clock or time.time  # WALL-CLOCK cadence — decoupled from the candle loop
        self._last_decision_ts = None
        self._next_interval_sec = interval_sec  # AI sets this each consult (<= interval_sec)

    def _get_client(self):
        if self._client is not None:
            return self._client
        import anthropic  # lazy — only needed when actually consulting Claude
        self._client = anthropic.Anthropic()
        return self._client

    def _decide_via_api(self, user):
        """Return (decision_dict, raw_text)."""
        client = self._get_client()
        resp = client.messages.create(
            model=self.model, max_tokens=self.max_tokens, system=_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[{"role": "user", "content": user}],
        )
        text = next(b.text for b in resp.content if getattr(b, "type", None) == "text")
        return _extract_json(text), text

    def _decide_via_cli(self, user):
        """Shell out to the local `claude` CLI (headless) — uses existing Claude Code auth,
        no API key. Returns (decision_dict, raw_envelope). Heavier per call but no extra billing."""
        import subprocess
        prompt = f"{_SYSTEM}\n\n{user}\n\nRespond with ONLY the JSON object, no prose."
        cmd = ["claude", "-p", prompt, "--output-format", "json"]
        if self.model:
            cmd += ["--model", self.model]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.cli_timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"claude cli rc={proc.returncode}: {proc.stderr[:300]}")
        env = json.loads(proc.stdout)
        if env.get("is_error"):
            raise RuntimeError(f"claude cli error: {str(env.get('result', ''))[:300]}")
        return _extract_json(str(env.get("result", ""))), proc.stdout

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

    def _build_context(self, candles, position):
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
        return {
            "charts": charts,
            "position": (position.side if position else "flat"),
            "max_check_sec": self.interval_sec,           # ceiling for next_check_in_sec
            "available_intervals": list(feed.INTERVALS),  # what you may request
        }

    def on_candle(self, candles, position):
        # Cadence: consult Claude only every interval_sec seconds of WALL-CLOCK time, so the
        # brain can poll faster (or slower) than the engine's candle interval. HOLD in between.
        now = self._clock()
        if self._last_decision_ts is not None and (now - self._last_decision_ts) < self._next_interval_sec:
            return Signal("HOLD", reason=f"waiting (next LLM check in ~{self._next_interval_sec:g}s)")
        ctx = self._build_context(candles, position)
        user = "Decide the trade. Charts:\n" + json.dumps(ctx)
        try:
            data, raw = self._decide_via_cli(user) if self.backend == "cli" else self._decide_via_api(user)
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
                      "requested_charts": self._requested},
            )
        except Exception as exc:  # missing key/package/CLI, network, bad JSON -> HOLD + error alert
            return Signal("HOLD", reason=f"llm unavailable: {exc}", error=str(exc))
