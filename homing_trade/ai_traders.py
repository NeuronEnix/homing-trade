"""AI traders — independent model "brains", assembled from config + an env-discovered registry.

Each brain is an `LlmTrader` with its own name (so it gets its own wallet and its own row on
the leaderboard), its own backend, and its own poll cadence. They are fully independent: enable
any subset and they trade side by side so you can compare them directly.

Two ways a provider is enabled, unified into one registry:

  1. The two BUILT-IN brains, driven by their typed `Config` fields (back-compat):
       AI_CLAUDE_CODE_IS_ENABLED / AI_CLAUDE_CODE_POLL_IN_SEC  -> llm_claude_code (cli backend)
       AI_ANTHROPIC_IS_ENABLED   / AI_ANTHROPIC_POLL_IN_SEC    -> llm_anthropic   (api backend)
     (config.from_env reads those env vars into Config.ai_*_enabled / ai_*_poll_sec.)

  2. ANY provider discovered generically from the environment (Phase 5 — multi-AI):
       AI_<NAME>_IS_ENABLED   (truthy: 1/true/yes/on)
       AI_<NAME>_BACKEND      (cli | api; defaults to the built-in's backend for known names)
       AI_<NAME>_POLL_IN_SEC  (seconds; defaults to the built-in's cadence, else DEFAULT_POLL_SEC)
       AI_<NAME>_MODEL        (defaults to cfg.llm_model)
     A provider spins up as strategy `llm_<name_lower>`. If both a built-in's Config field AND its
     env flag are set, the env flag is the explicit signal and wins.

A provider whose backend `LlmTrader` cannot drive (anything outside KNOWN_BACKENDS — the
`llm_backends` adapter registry: cli/api/openai/mistral/llama) is SKIPPED rather than mis-routed;
each adapter itself degrades to HOLD when its SDK/key is absent. The approved-name whitelist is
Phase 5 #3. This module is the single place that maps config + env -> AI strategy instances, kept
separate from the mechanical skills (engine) and the risk layer.
"""
import re

from homing_trade.llm_backends import BACKENDS
from homing_trade.skills.llm_trader import LlmTrader

# Built-in providers: NAME -> (backend, default_poll_sec). These are the registry/whitelist seed
# and keep working from their typed Config fields even with no AI_<NAME>_* env vars present.
BUILTIN_PROVIDERS = {
    "CLAUDE_CODE": ("cli", 3600),
    "ANTHROPIC": ("api", 900),
}

# Backends LlmTrader can drive — the adapter registry (cli/api/openai/mistral/llama, Phase 5 #2).
# A provider declaring any other backend is skipped at discovery so it never mis-routes; each
# adapter itself degrades to HOLD when its SDK/key is absent (llm_backends never-crash contract).
KNOWN_BACKENDS = set(BACKENDS)

# Poll cadence for a discovered provider that declares no AI_<NAME>_POLL_IN_SEC and is not built-in.
DEFAULT_POLL_SEC = 3600

_NAME_RE = re.compile(r"^AI_([A-Z0-9_]+)_IS_ENABLED$")
_TRUTHY = ("1", "true", "yes", "on")


def _truthy(v) -> bool:
    return isinstance(v, str) and v.strip().lower() in _TRUTHY


def _int(v, default):
    try:
        return int(float(v)) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def discover_providers(env) -> dict:
    """Scan `env` for AI_<NAME>_IS_ENABLED flags; return {NAME: {backend, poll_sec_env, model_env}}
    for each ENABLED provider whose backend is supported. Backend defaults to the built-in's for a
    known name; an unknown name with no/unsupported backend is dropped (not yet adapter-backed)."""
    specs = {}
    for key, val in env.items():
        m = _NAME_RE.match(key)
        if not m or not _truthy(val):
            continue
        name = m.group(1)
        builtin = BUILTIN_PROVIDERS.get(name)
        backend = (env.get(f"AI_{name}_BACKEND") or (builtin[0] if builtin else "")).strip().lower()
        if backend not in KNOWN_BACKENDS:
            continue                                   # unsupported/missing backend -> skip
        specs[name] = {
            "backend": backend,
            "poll_sec_env": env.get(f"AI_{name}_POLL_IN_SEC"),
            "model_env": env.get(f"AI_{name}_MODEL") or "",
            "builtin_poll": builtin[1] if builtin else DEFAULT_POLL_SEC,
        }
    return specs


def build_ai_traders(cfg, env=None):
    """Return the list of enabled AI trader strategies (possibly empty), sorted by name.

    Merges the two built-in brains (driven by their typed Config fields, for back-compat) with any
    providers discovered generically from `env` (defaults to the AI_* snapshot captured on the
    Config by from_env, NOT the live os.environ — so a bare Config() composes deterministically).
    When a built-in is enabled by both its Config field and its env flag, an EXPLICIT env poll/model
    wins; a bare env enable flag (no poll/model) keeps the Config-supplied value."""
    env = getattr(cfg, "ai_providers_env", None) or {} if env is None else env
    tfs = tuple(getattr(cfg, "ai_timeframes", ("15m", "1h", "4h")))
    chart_limit = getattr(cfg, "ai_chart_limit", 150)
    default_model = cfg.llm_model

    specs = {}  # NAME -> {backend, poll_sec, model}

    # 1) Back-compat: the two built-ins driven by their typed Config fields.
    if getattr(cfg, "ai_claude_code_enabled", False):
        specs["CLAUDE_CODE"] = {"backend": "cli",
                                "poll_sec": getattr(cfg, "ai_claude_code_poll_sec", 3600),
                                "model": default_model}
    if getattr(cfg, "ai_anthropic_enabled", False):
        specs["ANTHROPIC"] = {"backend": "api",
                              "poll_sec": getattr(cfg, "ai_anthropic_poll_sec", 900),
                              "model": default_model}

    # 2) Env-discovered registry (generalizes to any AI_<NAME>_*). An explicit env poll/model is the
    #    explicit signal and wins; a bare enable flag (no poll/model in env) keeps whatever step 1
    #    already supplied from the typed Config field, falling back to the built-in/global default.
    for name, d in discover_providers(env).items():
        prev = specs.get(name, {})
        specs[name] = {"backend": d["backend"],
                       "poll_sec": _int(d["poll_sec_env"], prev.get("poll_sec", d["builtin_poll"])),
                       "model": d["model_env"] or prev.get("model", default_model)}

    traders = []
    for name in sorted(specs):
        s = specs[name]
        traders.append(LlmTrader(
            name=f"llm_{name.lower()}", backend=s["backend"],
            model=s["model"], pair=cfg.pair_candles,
            interval_sec=s["poll_sec"], timeframes=tfs, chart_limit=chart_limit,
        ))
    return traders
