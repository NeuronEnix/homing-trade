"""Phase 5 #6: the multi-AI provider registry, end-to-end.

Unit-level coverage of the pieces lives in test_ai_traders.py (discovery / whitelist / Config
merge) and test_llm_backends.py (the adapter interface / usage / never-crash). THIS suite exercises
the registry as a whole along the real path an operator hits:

    AI_<NAME>_* env  ->  config.from_env (captures the AI_* snapshot)  ->  build_ai_traders
    ->  whitelist gate  ->  backend gate  ->  a configured LlmTrader  ->  on_candle degrades to
        HOLD when the provider's SDK/key is absent (never crashes the loop).
"""
import builtins
from homing_trade.config import Config, from_env
from homing_trade.ai_traders import build_ai_traders
from homing_trade.models import Candle


def candles(n=40, price=64000.0):
    return [Candle(open=price, high=price + 5, low=price - 5, close=price, volume=1,
                   time=1000 + i * 60000) for i in range(n)]


def PROV(interval, limit=150, start=None, end=None):  # chart provider stub — no network in tests
    return candles()


def _cfg_from_env(monkeypatch, **env):
    """Set AI_* env vars, run the real from_env capture, return the Config the engine would build."""
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return from_env(Config(), dotenv_path="/nonexistent")


# --- the full env -> from_env -> registry path ---
def test_builtin_brain_via_env_end_to_end(monkeypatch):
    cfg = _cfg_from_env(monkeypatch, AI_ANTHROPIC_IS_ENABLED="1", AI_ANTHROPIC_POLL_IN_SEC="450",
                        HT_LLM_MODEL="claude-opus-4-8")
    traders = build_ai_traders(cfg)
    assert [t.name for t in traders] == ["llm_anthropic"]
    t = traders[0]
    assert t.backend == "api" and t.interval_sec == 450 and t.model == "claude-opus-4-8"


def test_whitelisted_generic_provider_end_to_end(monkeypatch):
    cfg = _cfg_from_env(monkeypatch, AI_PROVIDERS_WHITELIST="grok",
                        AI_GROK_IS_ENABLED="1", AI_GROK_BACKEND="openai",
                        AI_GROK_POLL_IN_SEC="600", AI_GROK_MODEL="grok-2")
    t = next(t for t in build_ai_traders(cfg) if t.name == "llm_grok")
    assert t.backend == "openai" and t.interval_sec == 600 and t.model == "grok-2"


def test_several_providers_spin_up_with_distinct_wallets(monkeypatch):
    cfg = _cfg_from_env(monkeypatch, AI_PROVIDERS_WHITELIST="grok",
                        AI_CLAUDE_CODE_IS_ENABLED="1", AI_ANTHROPIC_IS_ENABLED="1",
                        AI_GROK_IS_ENABLED="1", AI_GROK_BACKEND="mistral")
    names = [t.name for t in build_ai_traders(cfg)]
    assert names == ["llm_anthropic", "llm_claude_code", "llm_grok"]   # sorted, distinct
    assert len(set(names)) == 3                                        # => distinct wallets


def test_unapproved_provider_never_spins_up(monkeypatch):
    # Enabled + valid backend, but not whitelisted -> the gate drops it before a brain is built.
    cfg = _cfg_from_env(monkeypatch, AI_EVIL_IS_ENABLED="1", AI_EVIL_BACKEND="api")
    assert build_ai_traders(cfg) == []


def test_unknown_backend_provider_never_spins_up(monkeypatch):
    cfg = _cfg_from_env(monkeypatch, AI_PROVIDERS_WHITELIST="grok",
                        AI_GROK_IS_ENABLED="1", AI_GROK_BACKEND="ollama")  # not an adapter
    assert build_ai_traders(cfg) == []


# --- the never-crash contract: a discovered provider with an absent SDK degrades to HOLD ---
def test_discovered_provider_degrades_to_hold_when_sdk_absent(monkeypatch):
    cfg = _cfg_from_env(monkeypatch, AI_PROVIDERS_WHITELIST="grok",
                        AI_GROK_IS_ENABLED="1", AI_GROK_BACKEND="mistral", AI_GROK_MODEL="x")
    t = next(t for t in build_ai_traders(cfg) if t.name == "llm_grok")
    t._provider = PROV                                    # stub charts (no network)

    real_import = builtins.__import__

    def _no_mistral(name, *a, **k):
        if name == "mistralai":
            raise ImportError("no mistralai")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_mistral)
    sig = t.on_candle(candles(), None)
    assert sig.action == "HOLD" and sig.error                 # degraded, did not crash


def test_no_ai_providers_means_no_brains(monkeypatch):
    # A clean environment (no AI_* flags) builds zero AI traders — the engine just runs mech skills.
    cfg = from_env(Config(), dotenv_path="/nonexistent")
    # there may be ambient AI_* vars in CI; assert no *generic* brain appears without a whitelist
    assert all(t.name in ("llm_claude_code", "llm_anthropic") for t in build_ai_traders(cfg))
