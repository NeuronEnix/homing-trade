"""Phase 11 #2 (first cut): the escalation policy — the single, deterministic, **testable** answer
to "when must the bot ask?".

`escalation_for(action, ctx, th) -> Verdict(level, reasons)` is PURE (a decision from facts), so it
is logged + replayable. It returns the HIGHEST level any trigger fires:

    ROUTINE   — an ordinary, in-envelope entry/exit.
    NOTABLE   — heads-up, no approval needed (first trade of the session, a regime flip, a stop exit).
    ESCALATION — high-stakes or novel; on the live feed (built later) this gates the action behind
                 owner approval. On the paper feed it is purely informational — paper narrates,
                 never asks.

FAIL-SAFE (the crux): a missing CORE fact, or a trigger that is configured-active but whose input is
absent, yields ESCALATION — the policy NEVER silently downgrades to ROUTINE when it cannot tell.
A trigger whose threshold is unset (None / 0 where noted) is simply not evaluated.

Triggers (spec §2): size (notional vs % of equity or an absolute cap), risk-posture change (always —
these map to PROTECTED_PROPOSAL_FIELDS), novelty (a strategy/symbol/side never traded before, or size
far above this strategy's recent baseline), and drawdown/volatility guardrails (approaching
max_daily_loss, a loss streak, a realized-vol spike). Novelty is computed mechanically from the audit
ledger by the caller — never from model self-assessment.

This module is a leaf (imports only stdlib), so the engine and the trade feed can both use it.
"""
from dataclasses import dataclass, field

ROUTINE, NOTABLE, ESCALATION = "ROUTINE", "NOTABLE", "ESCALATION"
_RANK = {ROUTINE: 0, NOTABLE: 1, ESCALATION: 2}


@dataclass(frozen=True)
class Thresholds:
    """Escalation knobs. Conservative defaults (escalate more; relax with data — spec §10). A None
    (or 0 where noted) threshold disables that trigger. The *risk* fields these reference are
    protected; these thresholds themselves are operator-tunable."""
    size_pct_of_equity: float | None = 0.25   # notional >= this fraction of equity -> escalate
    size_abs_cap: float = 0.0                  # notional >= this absolute cap -> escalate (0 = off)
    drawdown_frac: float | None = 0.7          # day loss >= this fraction of max_daily_loss
    vol_spike_mult: float | None = 1.5         # realized_vol >= this x vol_threshold
    loss_streak: int | None = 3               # >= this many consecutive losing trades
    novelty_k: float | None = 2.0              # notional > baseline_mean + k*baseline_std


@dataclass(frozen=True)
class Verdict:
    level: str
    reasons: tuple = field(default_factory=tuple)

    @property
    def is_escalation(self):
        return self.level == ESCALATION


_CORE_FIELDS = ("kind", "strategy")


def _num(v):
    """A finite number, or None. Strings/None/NaN/inf -> None (so a bad fact reads as 'missing')."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def escalation_for(action, ctx=None, th=None):
    """Return a Verdict for one proposed action. `action`: kind('entry'|'exit'|'posture_change'),
    strategy, symbol, side, notional, confidence, exit_reason, regime, decision_id. `ctx`: the
    mechanical facts the caller pulled from the ledger (equity, day_loss, max_daily_loss,
    realized_vol, vol_threshold, loss_streak, known_combos, baseline_mean, baseline_std, regime_flip,
    first_trade). `th`: Thresholds. Missing CORE action facts -> ESCALATION (fail-safe)."""
    th = th or Thresholds()
    ctx = ctx or {}
    # Fail-safe: no action, or missing a core field -> escalate. Never guess.
    if not isinstance(action, dict) or any(action.get(f) in (None, "") for f in _CORE_FIELDS):
        return Verdict(ESCALATION, ("missing-core-fact",))

    reasons = []
    esc, notable = [], []
    kind = action.get("kind")

    # 2. Risk-posture change ALWAYS escalates — these are PROTECTED_PROPOSAL_FIELDS; they can never be
    #    auto-applied, so a posture/leverage/risk change must always be a human decision.
    if kind == "posture_change":
        esc.append("risk-posture-change")

    if kind == "entry":
        notional = _num(action.get("notional"))
        # 1. Size — vs a % of equity, and vs an absolute cap.
        if th.size_pct_of_equity:
            equity = _num(ctx.get("equity"))
            if notional is None or equity is None:
                esc.append("size:missing-input")          # configured-active but unknowable -> escalate
            elif equity > 0 and notional >= th.size_pct_of_equity * equity:
                esc.append("size:>=%.0f%%-of-equity" % (th.size_pct_of_equity * 100))
        if th.size_abs_cap and th.size_abs_cap > 0:
            if notional is None:
                esc.append("size:missing-input")
            elif notional >= th.size_abs_cap:
                esc.append("size:>=abs-cap")
        # 3. Novelty — a strategy/symbol/side never traded before, or size far above this strategy's
        #    recent baseline. Computed mechanically by the caller from the audit ledger.
        combos = ctx.get("known_combos")
        if combos is not None:
            combo = (action.get("strategy"), action.get("symbol"), action.get("side"))
            if combo not in combos:
                esc.append("novelty:new-strategy/symbol/side")
        if th.novelty_k is not None:
            mean, std = _num(ctx.get("baseline_mean")), _num(ctx.get("baseline_std"))
            if mean is not None and std is not None and notional is not None and std > 0:
                if notional > mean + th.novelty_k * std:
                    esc.append("novelty:size>baseline+%.1fσ" % th.novelty_k)

    # 4. Drawdown / volatility guardrails (apply to any action).
    if th.drawdown_frac is not None:
        day_loss, cap = _num(ctx.get("day_loss")), _num(ctx.get("max_daily_loss"))
        if cap is not None and cap > 0 and day_loss is not None and day_loss >= th.drawdown_frac * cap:
            esc.append("drawdown:>=%.0f%%-of-kill-switch" % (th.drawdown_frac * 100))
    if th.vol_spike_mult is not None:
        vol, vth = _num(ctx.get("realized_vol")), _num(ctx.get("vol_threshold"))
        if vol is not None and vth is not None and vth > 0 and vol >= th.vol_spike_mult * vth:
            esc.append("vol-spike")
    if th.loss_streak is not None:
        streak = _num(ctx.get("loss_streak"))
        if streak is not None and streak >= th.loss_streak:
            esc.append("loss-streak:>=%d" % th.loss_streak)

    # NOTABLE (heads-up, no approval): first trade of the session, a regime flip, a stop exit.
    if ctx.get("first_trade"):
        notable.append("first-trade-of-session")
    if ctx.get("regime_flip"):
        notable.append("regime-flip")
    if kind == "exit" and action.get("exit_reason") in ("stop", "liquidation"):
        notable.append("exit:%s" % action.get("exit_reason"))

    if esc:
        return Verdict(ESCALATION, tuple(esc + notable))
    if notable:
        return Verdict(NOTABLE, tuple(notable))
    return Verdict(ROUTINE, ())


def max_level(a, b):
    """The higher-severity of two levels."""
    return a if _RANK[a] >= _RANK[b] else b
