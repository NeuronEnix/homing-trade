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
from homing_trade.skills.base import Strategy
from homing_trade.skills.indicators import ema, rsi
from homing_trade.models import Candle, Signal

_SCHEMA = {
    "type": "object",
    "properties": {
        "observation": {"type": "string"},   # what you SEE on the 1m + 15m charts
        "prediction": {"type": "string"},     # what you PREDICT price will do next
        "rationale": {"type": "string"},      # WHY that prediction leads to this decision
        "action": {"type": "string", "enum": ["LONG", "SHORT", "CLOSE", "HOLD"]},
        "confidence": {"type": "number"},
        "next_check_in_sec": {"type": "number"},  # how soon you want to look again
    },
    "required": ["observation", "prediction", "rationale", "action", "confidence", "next_check_in_sec"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You are a disciplined crypto-futures trader for the BTC/USDT perpetual (INR margin; "
    "leverage and position size are handled elsewhere — decide direction and timing only). "
    "You are given the 1-minute and 15-minute chart state. Default to HOLD unless there is a "
    "clear, multi-timeframe edge: trend alignment across BOTH timeframes, a momentum extreme "
    "to fade, or a clean breakout with expanding volatility. Avoid choppy, low-volatility, or "
    "conflicting tapes — they bleed fees, especially at high leverage. LONG only when flat and "
    "bullish; SHORT only when flat and bearish; CLOSE to exit an open position when the thesis "
    "is gone.\n\n"
    "Respond ONLY with the JSON schema, and be concrete:\n"
    "  observation — what you actually SEE on the 1m and 15m charts (trend, EMAs, RSI, volatility).\n"
    "  prediction  — what you PREDICT price will do next, and over what horizon.\n"
    "  rationale   — WHY that prediction leads to this action (tie observation -> prediction -> decision).\n"
    "  action, confidence (0-1).\n"
    "  next_check_in_sec — how many SECONDS until you want to see the market again. You will be "
    "re-consulted at most every 'max_check_sec' seconds (given in the data), but you may request "
    "SOONER (down to ~60) when a setup is developing, a breakout looks imminent, or you hold a "
    "position you need to watch closely. When the tape is quiet/choppy, ask for the max."
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
                 max_tokens=500, backend="api", cli_timeout=120,
                 pair="B-BTC_USDT", provider=None, name=None, clock=None, min_interval_sec=60):
        self.name = name or "llm_trader"   # per-instance so multiple brains get separate wallets
        self.model = model
        self.interval_sec = interval_sec        # the configured MAX gap between consults (seconds)
        self.min_interval_sec = min_interval_sec  # floor the AI can shorten to (watch closely)
        self._client = client
        self.max_tokens = max_tokens
        self.backend = backend          # "cli" (claude headless, no key) | "api" (anthropic SDK)
        self.cli_timeout = cli_timeout
        self.pair = pair
        self._provider = provider       # callable(interval)->[Candle]; defaults to the live feed
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

    def _provide(self, interval):
        if self._provider is not None:
            return self._provider(interval)
        from homing_trade.feed import get_candles
        return get_candles(self.pair, interval, limit=120)

    def _build_context(self, candles, position):
        c1 = self._provide("1m")
        c15 = self._provide("15m")
        tf_1m = _tf_summary([c.close for c in c1], c1) if len(c1) >= 21 else None
        tf_15m = _tf_summary([c.close for c in c15], c15) if len(c15) >= 21 else None
        return {
            "tf_1m": tf_1m,
            "tf_15m": tf_15m,
            "position": (position.side if position else "flat"),
            "max_check_sec": self.interval_sec,  # ceiling for next_check_in_sec
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
            reason = f"LLM({self.backend}) {action}: {rat}"
            if pred:
                reason += f" | predicts: {pred}"
            reason += f" [recheck ~{self._next_interval_sec:g}s]"
            return Signal(
                action,
                confidence=float(data.get("confidence", 0.5)),
                reason=reason[:400],
                indicators={"tf_1m": ctx["tf_1m"]["trend"] if ctx["tf_1m"] else "n/a",
                            "tf_15m": ctx["tf_15m"]["trend"] if ctx["tf_15m"] else "n/a"},
                raw=raw,
                meta={"observation": obs, "prediction": pred, "rationale": rat,
                      "next_check_in_sec": self._next_interval_sec},
            )
        except Exception as exc:  # missing key/package/CLI, network, bad JSON -> HOLD + error alert
            return Signal("HOLD", reason=f"llm unavailable: {exc}", error=str(exc))
