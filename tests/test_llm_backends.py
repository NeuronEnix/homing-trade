"""Phase 5 #2: backend adapters behind a common interface.

Every provider (cli/api/openai/mistral/llama) drives an LlmTrader through the same
`decide(backend, BackendRequest) -> (decision, raw)` contract, and degrades to HOLD (never crashes)
when its SDK/key/server is absent. SDK clients are injected here so the tests need no network and
no provider packages installed.
"""
import json
import pytest
from homing_trade import llm_backends
from homing_trade.llm_backends import BackendRequest, decide, BACKENDS, _extract_json
from homing_trade.skills.llm_trader import LlmTrader
from homing_trade.models import Candle

DECISION = {"observation": "o", "prediction": "p", "rationale": "r",
            "action": "LONG", "confidence": 0.7, "next_check_in_sec": 120}


def candles(n=60, price=64000.0):
    return [Candle(open=price, high=price + 5, low=price - 5, close=price, volume=1,
                   time=1000 + i * 60000) for i in range(n)]


def PROV(interval, limit=150, start=None, end=None):
    return candles()


def _req(prompt="Decide.", **kw):
    base = dict(prompt=prompt, system="SYS", model="m", max_tokens=200, schema={"type": "object"})
    base.update(kw)
    return BackendRequest(**base)


# --- anthropic-shaped fake (content blocks with .text) ---
class _Block:
    type = "text"
    def __init__(self, t): self.text = t


class _AnthropicResp:
    def __init__(self, t): self.content = [_Block(t)]


class _AnthropicClient:
    def __init__(self, payload): self._p = payload; self.calls = 0
    class _M:
        def __init__(self, outer): self.outer = outer
        def create(self, **kw):
            self.outer.calls += 1
            self.outer.last_kw = kw
            return _AnthropicResp(json.dumps(self.outer._p))
    @property
    def messages(self): return _AnthropicClient._M(self)


# --- OpenAI/Mistral-shaped fake (choices[0].message.content) ---
class _Msg:
    def __init__(self, c): self.message = type("M", (), {"content": c})


class _ChoicesResp:
    def __init__(self, t): self.choices = [_Msg(t)]


class _OpenAIClient:
    """Mimics openai.OpenAI(): client.chat.completions.create(...)."""
    def __init__(self, payload): self._p = payload; self.calls = 0; self.last_kw = None
    @property
    def chat(self):
        outer = self
        class _Completions:
            def create(self, **kw):
                outer.calls += 1; outer.last_kw = kw
                return _ChoicesResp(json.dumps(outer._p))
        return type("C", (), {"completions": _Completions()})


class _MistralClient:
    """Mimics mistralai.Mistral(): client.chat.complete(...)."""
    def __init__(self, payload): self._p = payload; self.calls = 0
    @property
    def chat(self):
        outer = self
        class _Chat:
            def complete(self, **kw):
                outer.calls += 1; outer.last_kw = kw
                return _ChoicesResp(json.dumps(outer._p))
        return _Chat()


def test_registry_has_the_five_backends():
    assert set(BACKENDS) == {"cli", "api", "openai", "mistral", "llama"}


def test_unknown_backend_raises_valueerror():
    with pytest.raises(ValueError):
        decide("ollama", _req())


def test_api_adapter_with_injected_client():
    c = _AnthropicClient(DECISION)
    data, raw = decide("api", _req(client=c))
    assert data["action"] == "LONG" and c.calls == 1
    assert c.last_kw["output_config"]["format"]["type"] == "json_schema"   # schema passed through


def test_openai_adapter_with_injected_client():
    c = _OpenAIClient(DECISION)
    data, raw = decide("openai", _req(client=c))
    assert data["action"] == "LONG" and c.calls == 1
    assert c.last_kw["response_format"] == {"type": "json_object"}


def test_mistral_adapter_with_injected_client():
    c = _MistralClient(DECISION)
    data, raw = decide("mistral", _req(client=c))
    assert data["action"] == "LONG" and c.calls == 1


def test_llama_adapter_with_injected_client():
    # llama reuses the OpenAI chat-completions shape (local OpenAI-compatible server).
    c = _OpenAIClient(DECISION)
    data, raw = decide("llama", _req(client=c))
    assert data["action"] == "LONG" and c.calls == 1


def test_cli_adapter_parses_envelope(monkeypatch):
    class _Proc:
        returncode = 0
        stdout = json.dumps({"is_error": False, "result": json.dumps(DECISION)})
        stderr = ""
    monkeypatch.setattr(llm_backends.subprocess, "run", lambda *a, **k: _Proc())
    data, raw = decide("cli", _req())
    assert data["action"] == "LONG"


def test_cli_adapter_raises_on_error_envelope(monkeypatch):
    class _Proc:
        returncode = 0
        stdout = json.dumps({"is_error": True, "result": "boom"})
        stderr = ""
    monkeypatch.setattr(llm_backends.subprocess, "run", lambda *a, **k: _Proc())
    with pytest.raises(RuntimeError):
        decide("cli", _req())


def test_extract_json_from_prose():
    assert _extract_json('noise {"action":"HOLD"} more')["action"] == "HOLD"


# --- end-to-end through LlmTrader: a generic backend drives a brain, and errors degrade to HOLD ---
def test_llmtrader_openai_backend_end_to_end():
    t = LlmTrader(backend="openai", client=_OpenAIClient(DECISION), provider=PROV, interval_sec=900)
    sig = t.on_candle(candles(), None)
    assert sig.action == "LONG" and sig.confidence == 0.7
    assert "LLM(openai)" in sig.reason


def test_llmtrader_backend_error_degrades_to_hold():
    class _Raises:
        @property
        def chat(self):
            raise RuntimeError("provider down")
    sig = LlmTrader(backend="openai", client=_Raises(), provider=PROV).on_candle(candles(), None)
    assert sig.action == "HOLD" and sig.error


def test_llmtrader_missing_sdk_degrades_to_hold(monkeypatch):
    # No client injected + the provider SDK genuinely absent -> import fails -> HOLD, never crash.
    import builtins
    real_import = builtins.__import__

    def _no_mistral(name, *a, **k):
        if name == "mistralai":
            raise ImportError("no mistralai")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_mistral)
    sig = LlmTrader(backend="mistral", provider=PROV).on_candle(candles(), None)
    assert sig.action == "HOLD" and sig.error
