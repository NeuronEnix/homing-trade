import json
from algotrading.agents.llm import LlmAgent
from algotrading.agents.base import AgentView
from algotrading.models import Candle


def candles_from(prices):
    return [Candle(open=p, high=p + 1, low=p - 1, close=p, volume=1.0, time=1000 + i * 60000)
            for i, p in enumerate(prices)]


class _Block:
    type = "text"
    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class _FakeMessages:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []
    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp(json.dumps(self._payload))


class _FakeClient:
    def __init__(self, payload):
        self.messages = _FakeMessages(payload)


def test_llm_parses_injected_client():
    client = _FakeClient({"stance": "bullish", "confidence": 0.8, "reason": "trend up"})
    v = LlmAgent("bull", client=client).assess(candles_from([float(x) for x in range(1, 40)]), None)
    assert isinstance(v, AgentView)
    assert v.stance == "BULLISH"  # upper-cased
    assert v.confidence == 0.8
    assert client.messages.calls and client.messages.calls[0]["model"] == "claude-opus-4-8"


def test_llm_no_client_no_anthropic_returns_neutral():
    # anthropic is not installed in the test venv -> lazy import raises -> NEUTRAL, no crash
    v = LlmAgent("risk").assess(candles_from([float(x) for x in range(1, 40)]), None)
    assert v.stance == "NEUTRAL"
    assert v.confidence == 0.0
    assert "llm unavailable" in v.reason


def test_llm_bad_json_returns_neutral():
    class BadMessages:
        def create(self, **kwargs):
            return _Resp("not json")
    class BadClient:
        messages = BadMessages()
    v = LlmAgent("bear", client=BadClient()).assess(candles_from([1.0, 2.0, 3.0]), None)
    assert v.stance == "NEUTRAL"
