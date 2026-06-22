"""AI traders — the two independent Claude "brains", assembled from config.

Each brain is an LlmTrader instance with its own name (so it gets its own wallet and its
own row on the leaderboard), its own backend, and its own poll cadence. They are fully
independent: enable either, both, or neither via the AI_* env flags. If both are on, they
trade side by side and you can compare CLI-Claude vs API-Claude directly.

  AI_CLAUDE_CODE_IS_ENABLED / AI_CLAUDE_CODE_POLL_IN_SEC   -> llm_claude_code (CLI backend)
  AI_ANTHROPIC_IS_ENABLED   / AI_ANTHROPIC_POLL_IN_SEC     -> llm_anthropic   (API backend)

This module is the single place that maps config -> AI strategy instances, kept separate
from the mechanical skills (engine.build_skills) and from the risk layer (risk.py).
"""
from homing_trade.skills.llm_trader import LlmTrader


def build_ai_traders(cfg):
    """Return the list of enabled AI trader strategies (possibly empty)."""
    traders = []
    if getattr(cfg, "ai_claude_code_enabled", False):
        traders.append(LlmTrader(
            name="llm_claude_code", backend="cli",
            model=cfg.llm_model, pair=cfg.pair_candles,
            interval_sec=getattr(cfg, "ai_claude_code_poll_sec", 3600),
        ))
    if getattr(cfg, "ai_anthropic_enabled", False):
        traders.append(LlmTrader(
            name="llm_anthropic", backend="api",
            model=cfg.llm_model, pair=cfg.pair_candles,
            interval_sec=getattr(cfg, "ai_anthropic_poll_sec", 900),
        ))
    return traders
