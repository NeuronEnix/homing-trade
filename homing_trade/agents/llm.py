import json
from homing_trade.agents.base import Agent, AgentView
from homing_trade.skills.indicators import ema, rsi

_ROLE_PROMPT = {
    "bull": ("You are a BULLISH crypto futures analyst. Make the strongest honest case for "
             "going long BTC/INR given the data. Respond ONLY with the JSON schema."),
    "bear": ("You are a BEARISH crypto futures analyst. Make the strongest honest case for "
             "caution or going short BTC/INR given the data. Respond ONLY with the JSON schema."),
    "risk": ("You are a RISK SUPERVISOR for an automated paper-trading bot. Your job is to VETO "
             "trades when conditions are too risky (high volatility, unclear trend). Be BEARISH "
             "(a veto) only when risk is genuinely elevated. Respond ONLY with the JSON schema."),
}

_SCHEMA = {
    "type": "object",
    "properties": {
        "stance": {"type": "string", "enum": ["BULLISH", "BEARISH", "NEUTRAL"]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["stance", "confidence", "reason"],
    "additionalProperties": False,
}


class LlmAgent(Agent):
    """Optional Claude-backed agent. OFF by default — needs ANTHROPIC_API_KEY and costs money
    per call. Any failure degrades gracefully to a NEUTRAL view."""

    def __init__(self, role: str, model: str = "claude-opus-4-8", client=None, max_tokens: int = 400):
        self.role = role
        self.name = f"llm_{role}"
        self.model = model
        self._client = client
        self.max_tokens = max_tokens

    def _get_client(self):
        if self._client is not None:
            return self._client
        import anthropic  # lazy — only needed in live LLM mode
        self._client = anthropic.Anthropic()
        return self._client

    def _build_prompt(self, candles, position):
        closes = [c.close for c in candles]
        last = closes[-1] if closes else 0.0
        f, s, r = ema(closes, 9), ema(closes, 21), rsi(closes, 14)
        pos = "long" if (position is not None and position.side == "LONG") else "flat"
        recent = ", ".join(f"{c:.0f}" for c in closes[-10:])
        return (f"BTC/INR. Last close {last:.0f}. EMA9={f}, EMA21={s}, RSI14={r}. "
                f"Current position: {pos}. Recent closes: {recent}. Give your stance.")

    def assess(self, candles, position):
        try:
            client = self._get_client()
            resp = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=_ROLE_PROMPT.get(self.role, _ROLE_PROMPT["risk"]),
                output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
                messages=[{"role": "user", "content": self._build_prompt(candles, position)}],
            )
            text = next(b.text for b in resp.content if getattr(b, "type", None) == "text")
            data = json.loads(text)
            return AgentView(str(data["stance"]).upper(), float(data["confidence"]), str(data["reason"]))
        except Exception as exc:  # missing anthropic, no key, network, bad JSON — all -> neutral
            return AgentView("NEUTRAL", 0.0, f"llm unavailable: {exc}")
