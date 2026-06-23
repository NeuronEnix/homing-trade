"""Phase 11 #1: the `#paper-trade` narration channel.

NARRATE-ONLY: it POSTS what the bot did and why to the paper-trade feed — it never asks for approval
(paper just runs). It builds the Phase-11 message contract (spec §3): **what** (action/symbol/side/
size) · **why** (the AI thesis + the mechanical reason) · **risk** (notional/leverage/stop) ·
**decision_id** (so the replay/audit tool can reconstruct it) · **level**. The level comes from the
shared, testable escalation policy (`escalation.escalation_for`); on the paper feed it is purely
informational (a heads-up at NOTABLE, a louder flag at ESCALATION). The live feed (built later)
reuses this exact contract + policy to actually GATE actions behind owner approval.

Default-OFF and degrade-safe, exactly like `comms`: no webhook configured -> `enabled` is False ->
`narrate()` is a no-op that never raises and never touches the network. The HTTP poster is injectable
so tests run offline. Channel naming convention: `<word>-trade` (spec §1)."""
import os

from homing_trade.escalation import escalation_for, Thresholds, ROUTINE, NOTABLE, ESCALATION

LEVEL_EMOJI = {ROUTINE: "▫️", NOTABLE: "🔸", ESCALATION: "🚨"}
LEVEL_COLOR = {ROUTINE: 0x95A5A6, NOTABLE: 0xF1C40F, ESCALATION: 0xE74C3C}  # grey / amber / red


def thresholds_from_cfg(cfg):
    """Build the escalation Thresholds from operator-tunable config (sane conservative defaults)."""
    g = lambda n, d: getattr(cfg, n, d)
    return Thresholds(
        size_pct_of_equity=g("esc_size_pct_of_equity", 0.25),
        size_abs_cap=g("esc_size_abs_cap", 0.0),
        drawdown_frac=g("esc_drawdown_frac", 0.7),
        vol_spike_mult=g("esc_vol_spike_mult", 1.5),
        loss_streak=g("esc_loss_streak", 3),
        novelty_k=g("esc_novelty_k", 2.0),
    )


def _fmt_num(v, places=2):
    try:
        return f"{float(v):,.{places}f}"
    except (TypeError, ValueError):
        return "—"


def format_message(action, why, verdict):
    """Pure: build the Discord webhook payload (an embed) for one narration. Testable without a
    network. `action` is the trade descriptor; `why` the human-readable rationale; `verdict` the
    escalation Verdict."""
    level = verdict.level
    strat = action.get("strategy", "?")
    word = {"entry": action.get("side", "OPEN"), "exit": "CLOSE",
            "posture_change": "POSTURE"}.get(action.get("kind"), action.get("kind", "?"))
    sym = action.get("symbol", "")
    title = f"{LEVEL_EMOJI.get(level, '')} {level} · {strat} {word} {sym}".strip()

    fields = []
    # WHAT
    what = []
    if action.get("side"):
        what.append(action["side"])
    if action.get("size") is not None:
        what.append(f"size {_fmt_num(action['size'], 6)}")
    if action.get("price") is not None:
        what.append(f"@ {_fmt_num(action['price'])}")
    if action.get("pnl") is not None:
        what.append(f"pnl {_fmt_num(action['pnl'])}")
    if action.get("exit_reason"):
        what.append(f"({action['exit_reason']})")
    if what:
        fields.append({"name": "What", "value": " ".join(what), "inline": False})
    # WHY
    if why:
        fields.append({"name": "Why", "value": str(why)[:900], "inline": False})
    # RISK
    risk = []
    if action.get("notional") is not None:
        risk.append(f"notional {_fmt_num(action['notional'])}")
    if action.get("leverage") is not None:
        risk.append(f"{_fmt_num(action['leverage'], 0)}x")
    if action.get("stop") is not None:
        risk.append(f"stop {_fmt_num(action['stop'])}")
    if action.get("confidence") is not None:
        risk.append(f"conf {_fmt_num(action['confidence'])}")
    if risk:
        fields.append({"name": "Risk", "value": " · ".join(risk), "inline": False})
    # WHY-ASKED (the escalation triggers) + decision_id for replay
    if verdict.reasons:
        fields.append({"name": "Flags", "value": ", ".join(verdict.reasons), "inline": False})
    if action.get("decision_id"):
        fields.append({"name": "decision_id", "value": f"`{action['decision_id']}`", "inline": False})

    embed = {"title": title, "color": LEVEL_COLOR.get(level, 0x95A5A6), "fields": fields}
    return {"embeds": [embed]}


class TradeFeed:
    """The paper-trade narrator. Construct once per run; call narrate() per trade. No-op (returns
    None) unless `paper_feed_enabled` AND a webhook is configured."""

    def __init__(self, cfg, *, poster=None, dotenv_path=".env"):
        self.cfg = cfg
        self._poster = poster
        webhook_env = getattr(cfg, "paper_feed_webhook_env", "PAPER_TRADE_WEBHOOK_URL")
        if poster is not None:
            # Tests inject a poster — treat as configured so format/level paths are exercised offline.
            self.webhook = getattr(cfg, "paper_feed_webhook", "") or "injected://paper-trade"
        else:
            from homing_trade.dotenv import load_dotenv
            load_dotenv(dotenv_path)
            self.webhook = os.environ.get(webhook_env, "")
        self.enabled = bool(getattr(cfg, "paper_feed_enabled", False) and self.webhook)
        self._th = thresholds_from_cfg(cfg)

    def level_for(self, action, ctx=None):
        """The escalation Verdict for an action — pure, even when the feed is disabled."""
        return escalation_for(action, ctx, self._th)

    def narrate(self, action, why="", ctx=None):
        """Post one narration. Returns the level posted (str), or None if disabled / post failed.
        Never raises (a feed failure must never disturb the trading loop)."""
        if not self.enabled:
            return None
        try:
            verdict = self.level_for(action, ctx)
            payload = format_message(action, why, verdict)
            self._post(payload)
            return verdict.level
        except Exception:
            return None

    def _post(self, payload):
        if self._poster is not None:
            self._poster(self.webhook, payload)
            return
        import requests
        requests.post(self.webhook, json=payload, timeout=10)
