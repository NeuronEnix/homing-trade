from homing_trade.ai_traders import build_ai_traders, discover_providers
from homing_trade.config import Config


# --- back-compat: the two built-ins driven by their typed Config fields (env={} = deterministic) ---
def test_none_enabled_by_default():
    assert build_ai_traders(Config(), env={}) == []


def test_claude_code_only():
    ts = build_ai_traders(Config(ai_claude_code_enabled=True, ai_claude_code_poll_sec=30), env={})
    assert len(ts) == 1
    assert ts[0].name == "llm_claude_code"
    assert ts[0].backend == "cli"
    assert ts[0].interval_sec == 30


def test_anthropic_only():
    ts = build_ai_traders(Config(ai_anthropic_enabled=True, ai_anthropic_poll_sec=20), env={})
    assert len(ts) == 1
    assert ts[0].name == "llm_anthropic"
    assert ts[0].backend == "api"
    assert ts[0].interval_sec == 20


def test_both_run_independently():
    ts = build_ai_traders(Config(ai_claude_code_enabled=True, ai_anthropic_enabled=True), env={})
    assert len(ts) == 2
    assert {t.name for t in ts} == {"llm_claude_code", "llm_anthropic"}
    assert {t.backend for t in ts} == {"cli", "api"}
    # distinct names => distinct wallets => they trade independently
    assert ts[0].name != ts[1].name


# --- Phase 5 #1: generic env-discovered registry ---
def test_builtins_discovered_from_env_only():
    # No Config fields set; the built-ins are picked up purely from their AI_<NAME>_* env flags.
    env = {"AI_CLAUDE_CODE_IS_ENABLED": "true", "AI_ANTHROPIC_IS_ENABLED": "1"}
    ts = build_ai_traders(Config(), env=env)
    assert {t.name for t in ts} == {"llm_claude_code", "llm_anthropic"}
    by = {t.name: t for t in ts}
    assert by["llm_claude_code"].backend == "cli" and by["llm_claude_code"].interval_sec == 3600
    assert by["llm_anthropic"].backend == "api" and by["llm_anthropic"].interval_sec == 900


def test_generic_provider_discovered():
    env = {"AI_GROK_IS_ENABLED": "yes", "AI_GROK_BACKEND": "api",
           "AI_GROK_POLL_IN_SEC": "450", "AI_GROK_MODEL": "grok-2"}
    ts = build_ai_traders(Config(), env=env)
    assert len(ts) == 1
    t = ts[0]
    assert t.name == "llm_grok" and t.backend == "api"
    assert t.interval_sec == 450 and t.model == "grok-2"


def test_generic_provider_model_defaults_to_cfg_llm_model():
    env = {"AI_GROK_IS_ENABLED": "1", "AI_GROK_BACKEND": "api"}
    ts = build_ai_traders(Config(llm_model="claude-test-model"), env=env)
    assert ts[0].model == "claude-test-model"
    assert ts[0].interval_sec == 3600                       # DEFAULT_POLL_SEC for an unknown name


def test_disabled_flag_is_not_discovered():
    env = {"AI_GROK_IS_ENABLED": "0", "AI_GROK_BACKEND": "api"}
    assert build_ai_traders(Config(), env=env) == []


def test_unknown_backend_is_skipped_not_misrouted():
    # openai/mistral adapters don't exist yet (Phase 5 #2); an unsupported backend must not
    # silently route to the Anthropic API path — the provider is dropped.
    env = {"AI_LLAMA_IS_ENABLED": "1", "AI_LLAMA_BACKEND": "ollama"}
    assert build_ai_traders(Config(), env=env) == []
    assert discover_providers(env) == {}


def test_missing_backend_for_unknown_name_is_skipped():
    # A generic name with no backend declared can't be instantiated -> dropped (no default backend).
    env = {"AI_MYSTERY_IS_ENABLED": "1"}
    assert build_ai_traders(Config(), env=env) == []


def test_env_overrides_builtin_poll_and_model():
    # When a built-in is enabled by BOTH its Config field and its env flag, env wins (explicit).
    env = {"AI_CLAUDE_CODE_IS_ENABLED": "1", "AI_CLAUDE_CODE_POLL_IN_SEC": "77",
           "AI_CLAUDE_CODE_MODEL": "claude-cli-override"}
    ts = build_ai_traders(Config(ai_claude_code_enabled=True, ai_claude_code_poll_sec=30), env=env)
    assert len(ts) == 1
    assert ts[0].interval_sec == 77 and ts[0].model == "claude-cli-override"


def test_traders_are_sorted_by_name_deterministically():
    env = {"AI_CLAUDE_CODE_IS_ENABLED": "1", "AI_ANTHROPIC_IS_ENABLED": "1",
           "AI_GROK_IS_ENABLED": "1", "AI_GROK_BACKEND": "api"}
    names = [t.name for t in build_ai_traders(Config(), env=env)]
    assert names == sorted(names)
    assert names == ["llm_anthropic", "llm_claude_code", "llm_grok"]


def test_discover_providers_ignores_unrelated_env():
    env = {"PATH": "/usr/bin", "AI_TIMEFRAMES": "15m", "HT_LLM_MODEL": "x",
           "AI_GROK_BACKEND": "api"}                        # no _IS_ENABLED -> nothing
    assert discover_providers(env) == {}


def test_bad_poll_value_falls_back_to_default():
    env = {"AI_GROK_IS_ENABLED": "1", "AI_GROK_BACKEND": "api", "AI_GROK_POLL_IN_SEC": "notanumber"}
    ts = build_ai_traders(Config(), env=env)
    assert ts[0].interval_sec == 3600


def test_bare_env_flag_keeps_config_supplied_poll():
    # Built-in enabled by its Config field with a custom poll; env supplies ONLY the enable flag
    # (no AI_*_POLL_IN_SEC) -> the operator's Config cadence must survive, not snap to the default.
    env = {"AI_ANTHROPIC_IS_ENABLED": "1"}                  # flag only, no poll/model
    ts = build_ai_traders(Config(ai_anthropic_enabled=True, ai_anthropic_poll_sec=120), env=env)
    assert len(ts) == 1 and ts[0].interval_sec == 120       # not the 900 built-in default


def test_no_env_arg_reads_config_snapshot_not_global(monkeypatch):
    # build_ai_traders must read cfg.ai_providers_env, never the live os.environ -> a bare Config()
    # is deterministic even if AI_<NAME> vars are exported in the ambient process environment.
    monkeypatch.setenv("AI_GROK_IS_ENABLED", "1")
    monkeypatch.setenv("AI_GROK_BACKEND", "api")
    assert build_ai_traders(Config()) == []                 # bare Config -> empty snapshot -> none


def test_from_env_snapshot_drives_discovery(monkeypatch):
    # The single env->Config layer (from_env) captures AI_* and the registry discovers from it.
    from homing_trade.config import from_env, Config
    monkeypatch.setenv("AI_GROK_IS_ENABLED", "1")
    monkeypatch.setenv("AI_GROK_BACKEND", "api")
    monkeypatch.setenv("AI_GROK_POLL_IN_SEC", "300")
    cfg = from_env(Config(), dotenv_path="/nonexistent")
    assert cfg.ai_providers_env.get("AI_GROK_IS_ENABLED") == "1"
    ts = build_ai_traders(cfg)
    assert [t.name for t in ts] == ["llm_grok"] and ts[0].interval_sec == 300
