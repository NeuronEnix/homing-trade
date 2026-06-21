"""LLM-driven multi-timeframe trading strategy.

Claude reads the 1-minute AND 15-minute chart state and decides WHEN to trade
(LONG / SHORT / CLOSE / HOLD) — direction and timing only. Position size, leverage,
and the daily risk limits are decided elsewhere (engine + DailyRiskGuard).

It calls the Anthropic API per decision (needs ANTHROPIC_API_KEY; costs money), so it
consults Claude only every `interval_min` minutes and HOLDs in between. With no key,
no `anthropic` package, or any error, it degrades to HOLD — it never crashes the bot.
"""
import json
from homing_trade.skills.base import Strategy
from homing_trade.skills.indicators import ema, rsi
from homing_trade.models import Candle, Signal

_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["LONG", "SHORT", "CLOSE", "HOLD"]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["action", "confidence", "reason"],
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
    "is gone. Respond ONLY with the JSON schema."
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

    def __init__(self, model="claude-opus-4-8", interval_min=15, client=None, max_tokens=500):
        self.model = model
        self.interval_min = interval_min
        self._client = client
        self.max_tokens = max_tokens
        self._last_decision_time = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        import anthropic  # lazy — only needed when actually consulting Claude
        self._client = anthropic.Anthropic()
        return self._client

    def _build_context(self, candles, position):
        closes_1m = [c.close for c in candles]
        c15 = resample(candles, 15)
        tf_15m = _tf_summary([c.close for c in c15], c15) if len(c15) >= 21 else None
        return {
            "tf_1m": _tf_summary(closes_1m, candles),
            "tf_15m": tf_15m,
            "position": (position.side if position else "flat"),
        }

    def on_candle(self, candles, position):
        cur = candles[-1]
        # Cadence: consult Claude only every interval_min minutes; HOLD in between (cost control).
        if self._last_decision_time is not None:
            elapsed = (cur.time - self._last_decision_time) / 60000
            if elapsed < self.interval_min:
                return Signal("HOLD", reason=f"waiting {elapsed:.0f}/{self.interval_min}m to next LLM check")
        ctx = self._build_context(candles, position)
        try:
            client = self._get_client()
            resp = client.messages.create(
                model=self.model, max_tokens=self.max_tokens, system=_SYSTEM,
                output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
                messages=[{"role": "user", "content": "Decide the trade. Charts:\n" + json.dumps(ctx)}],
            )
            text = next(b.text for b in resp.content if getattr(b, "type", None) == "text")
            data = json.loads(text)
            self._last_decision_time = cur.time
            action = str(data["action"]).upper()
            is_long = position is not None and position.side == "LONG"
            # guard the mapping so the engine never gets an impossible action
            if action == "LONG" and position is not None:
                action = "HOLD"
            if action == "CLOSE" and position is None:
                action = "HOLD"
            if action not in ("LONG", "SHORT", "CLOSE", "HOLD"):
                action = "HOLD"
            return Signal(
                action,
                confidence=float(data.get("confidence", 0.5)),
                reason="LLM: " + str(data.get("reason", ""))[:200],
                indicators={"tf_1m": ctx["tf_1m"]["trend"],
                            "tf_15m": ctx["tf_15m"]["trend"] if ctx["tf_15m"] else "n/a"},
            )
        except Exception as exc:  # missing key/package, network, bad JSON -> HOLD, never crash
            return Signal("HOLD", reason=f"llm unavailable: {exc}")
