"""Phase 11 #1: the #paper-trade narration feed. Offline (injected poster) + default-OFF, so the
suite stays network-free."""
import dataclasses
from homing_trade.config import Config
from homing_trade.trade_feed import TradeFeed, format_message, thresholds_from_cfg
from homing_trade.escalation import escalation_for, ROUTINE, NOTABLE, ESCALATION


class _Poster:
    def __init__(self):
        self.calls = []
    def __call__(self, url, payload):
        self.calls.append((url, payload))


def _entry(**kw):
    base = dict(kind="entry", strategy="ma_trend", symbol="B-BTC_USDT", side="LONG",
                size=0.01, price=64000.0, notional=100.0, leverage=10, stop=62700.0,
                confidence=0.6, decision_id="abc123")
    base.update(kw)
    return base


# --- default-OFF + degrade-safe -----------------------------------------------------------------
def test_disabled_by_default_is_noop():
    feed = TradeFeed(Config(), poster=_Poster())          # paper_feed_enabled defaults False
    assert feed.enabled is False
    assert feed.narrate(_entry(), "thesis") is None


def test_no_webhook_means_disabled(monkeypatch):
    monkeypatch.delenv("PAPER_TRADE_WEBHOOK_URL", raising=False)
    cfg = dataclasses.replace(Config(), paper_feed_enabled=True)
    feed = TradeFeed(cfg, dotenv_path="/nonexistent/.env")  # no poster, no webhook -> disabled
    assert feed.enabled is False
    assert feed.narrate(_entry()) is None


def test_enabled_posts_and_returns_level():
    p = _Poster()
    cfg = dataclasses.replace(Config(), paper_feed_enabled=True)
    feed = TradeFeed(cfg, poster=p)
    assert feed.enabled is True
    ctx = dict(equity=5000.0, known_combos={("ma_trend", "B-BTC_USDT", "LONG")})
    level = feed.narrate(_entry(notional=100.0), "trend up, MA cross", ctx)
    assert level == ROUTINE and len(p.calls) == 1
    url, payload = p.calls[0]
    assert "embeds" in payload


def test_narrate_never_raises_on_poster_error():
    def boom(url, payload):
        raise RuntimeError("network down")
    cfg = dataclasses.replace(Config(), paper_feed_enabled=True)
    feed = TradeFeed(cfg, poster=boom)
    assert feed.narrate(_entry(), "x") is None     # swallowed, returns None


# --- the message contract (what / why / risk / decision_id / level) -----------------------------
def test_message_contract_fields_present():
    v = escalation_for(_entry(), dict(equity=5000.0,
                                      known_combos={("ma_trend", "B-BTC_USDT", "LONG")}),
                       thresholds_from_cfg(Config()))
    payload = format_message(_entry(), "the AI thesis here", v)
    embed = payload["embeds"][0]
    names = {f["name"] for f in embed["fields"]}
    assert {"What", "Why", "Risk", "decision_id"} <= names
    did = next(f["value"] for f in embed["fields"] if f["name"] == "decision_id")
    assert "abc123" in did


def test_escalation_level_colors_and_flags():
    p = _Poster()
    cfg = dataclasses.replace(Config(), paper_feed_enabled=True)
    feed = TradeFeed(cfg, poster=p)
    # big notional vs small equity -> ESCALATION; the flags + red color must show
    level = feed.narrate(_entry(notional=900.0), "oversized",
                         dict(equity=1000.0, known_combos={("ma_trend", "B-BTC_USDT", "LONG")}))
    assert level == ESCALATION
    embed = p.calls[0][1]["embeds"][0]
    assert embed["color"] == 0xE74C3C
    assert any(f["name"] == "Flags" for f in embed["fields"])


def test_stop_exit_narrates_notable():
    p = _Poster()
    cfg = dataclasses.replace(Config(), paper_feed_enabled=True)
    feed = TradeFeed(cfg, poster=p)
    action = dict(kind="exit", strategy="bollinger", symbol="B-BTC_USDT", side="LONG",
                  size=0.01, price=62000.0, pnl=-107.4, exit_reason="stop", decision_id="d9")
    level = feed.narrate(action, "stopped out", dict(equity=4900.0))
    assert level == NOTABLE
    embed = p.calls[0][1]["embeds"][0]
    assert "CLOSE" in embed["title"]
