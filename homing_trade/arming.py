"""Phase 10 #1: the explicit live-trading arming gate.

THE single decision point for whether the engine may place REAL orders. The default is PAPER: real
money requires the operator to DELIBERATELY flip flags AND provide API keys, and even the first
"live" step only simulates. This module is pure decision + fail-closed guard logic — it neither
places orders, flips any flag, nor wires live execution. Building it arms NOTHING; merging it changes
nothing operationally (default config → PAPER).

Modes (resolve_mode), in increasing risk:
  PAPER         — live_enabled is False (DEFAULT). The simulated Broker. No real money, ever.
  LIVE_DRY_RUN  — live_enabled=True, but live_dry_run=True OR keys missing. A live wiring that still
                  SIMULATES (no exchange call). The first deliberate step; proves the path at zero risk.
  LIVE          — live_enabled=True AND live_dry_run=False AND API keys present. Real orders.

The gate FAILS CLOSED. `assert_safe_to_arm` refuses LIVE unless every precondition holds (keys,
master switch on, a positive max_daily_loss kill-switch, a positive live_capital_cap). And because
the live EXECUTION layer (PositionManager placing/reconciling real orders) is not integrated yet,
`select_broker` REFUSES to run any live mode rather than trade through an incomplete path — so an
operator who flips the flags today gets a clear stop, never a half-armed live trade.
"""
from homing_trade.broker import Broker

PAPER = "paper"
LIVE_DRY_RUN = "live-dry-run"
LIVE = "live"


def resolve_mode(*, live_enabled, live_dry_run, keys_present):
    """Pure mode resolution from the three inputs. Defaults bias to the safest reachable mode:
    enabled-but-dry-run, or enabled-without-keys, both resolve to LIVE_DRY_RUN (never LIVE)."""
    if not live_enabled:
        return PAPER
    # Strict-bool on dry-run: ONLY an explicit `False` (not None/0/"") counts as "real orders wanted".
    # Any other value stays in simulation — the safe direction for a real-money gate.
    if live_dry_run is not False or not keys_present:
        return LIVE_DRY_RUN
    return LIVE


def keys_present_in_env(cfg, *, dotenv_path=".env"):
    """True only if BOTH CoinDCX key + secret are configured. Never returns or logs the values."""
    from homing_trade.dotenv import coindcx_keys
    key, secret = coindcx_keys(cfg, dotenv_path=dotenv_path)
    return bool(key and secret)


def arming_problems(cfg, *, keys_present):
    """The list of reasons LIVE is unsafe to arm right now (empty = safe). Used by assert + the
    UI/logs so the operator sees exactly what's missing before anything goes live."""
    problems = []
    if not keys_present:
        problems.append("no CoinDCX API keys configured")
    if not getattr(cfg, "trading_enabled", True):
        problems.append("trading_enabled (master switch) is False")
    if not (getattr(cfg, "max_daily_loss", 0) or 0) > 0:
        problems.append("max_daily_loss kill-switch is not set (> 0 required)")
    if not (getattr(cfg, "live_capital_cap", 0) or 0) > 0:
        problems.append("live_capital_cap is not set (> 0 required)")
    return problems


def assert_safe_to_arm(cfg, *, keys_present):
    """Resolve the mode and, for LIVE, raise PermissionError unless every precondition holds.
    PAPER and LIVE_DRY_RUN always pass (no real money is at stake in either). Returns the mode."""
    mode = resolve_mode(live_enabled=getattr(cfg, "live_enabled", False),
                        live_dry_run=getattr(cfg, "live_dry_run", True),
                        keys_present=keys_present)
    if mode == LIVE:
        problems = arming_problems(cfg, keys_present=keys_present)
        if problems:
            raise PermissionError("refusing to arm LIVE — " + "; ".join(problems))
    return mode


def select_broker(cfg, *, dotenv_path=".env"):
    """The broker the engine should trade through, chosen by the arming gate. Returns (broker, mode).

    Default config → (paper Broker, PAPER). For any LIVE mode the gate has passed its preconditions,
    but the live EXECUTION integration is not built yet, so this FAILS SAFE with NotImplementedError
    rather than trading through an incomplete path. So enabling the flags today stops the bot loudly;
    it never silently half-trades real money."""
    keys = keys_present_in_env(cfg, dotenv_path=dotenv_path)
    mode = assert_safe_to_arm(cfg, keys_present=keys)
    if mode == PAPER:
        return Broker(cfg.fee, cfg.slippage), PAPER
    raise NotImplementedError(
        f"arming resolved to '{mode}', but live execution is not integrated yet (Phase 10 #2). "
        "Refusing to run live; the bot stays paper until that lands.")
