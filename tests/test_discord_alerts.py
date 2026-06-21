from homing_trade.notify import DiscordNotifier, build_notifier
from homing_trade.config import Config

HOOK = "https://discord.com/api/webhooks/123/abc"


def test_discord_formats_and_posts():
    sent = []
    n = DiscordNotifier(HOOK, poster=lambda url, payload: sent.append((url, payload)))
    n.notify("trade", "grid OPEN", "buy 0.001 @ 6000000")
    url, payload = sent[0]
    assert url == HOOK
    assert "content" in payload
    assert "grid OPEN" in payload["content"] and "buy 0.001" in payload["content"]
    assert payload["content"].startswith("💱")  # trade icon


def test_discord_swallows_poster_error():
    def boom(url, payload):
        raise RuntimeError("network down")
    DiscordNotifier(HOOK, poster=boom).notify("info", "t", "m")  # must NOT raise


def test_build_notifier_discord_reads_env(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", HOOK)
    n = build_notifier(Config(alert_mode="discord"))
    assert isinstance(n, DiscordNotifier)
    assert n.webhook_url == HOOK
