from homing_trade.notify import TelegramNotifier, build_notifier
from homing_trade.config import Config
from homing_trade.daemon import cfg_from_env


def test_telegram_formats_and_posts():
    sent = []
    n = TelegramNotifier("TOK", "CHAT", poster=lambda url, payload: sent.append((url, payload)))
    n.notify("trade", "ma_trend OPEN", "buy 0.001 @ 6000000")
    url, payload = sent[0]
    assert url == "https://api.telegram.org/botTOK/sendMessage"
    assert payload["chat_id"] == "CHAT"
    assert "ma_trend OPEN" in payload["text"] and "buy 0.001" in payload["text"]
    assert payload["text"].startswith("💱")  # trade icon


def test_telegram_swallows_poster_error():
    def boom(url, payload):
        raise RuntimeError("network down")
    TelegramNotifier("TOK", "CHAT", poster=boom).notify("info", "t", "m")  # must NOT raise


def test_build_notifier_telegram_reads_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TOK")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "CHAT")
    n = build_notifier(Config(alert_mode="telegram"))
    assert isinstance(n, TelegramNotifier)
    assert n.token == "TOK" and n.chat_id == "CHAT"


def test_cfg_from_env_applies_alert_mode(monkeypatch):
    monkeypatch.setenv("HT_ALERT_MODE", "telegram")
    cfg = cfg_from_env(Config(), dotenv_path="/no/such/.env")
    assert cfg.alert_mode == "telegram"


def test_cfg_from_env_no_override_when_unset(monkeypatch):
    monkeypatch.delenv("HT_ALERT_MODE", raising=False)
    cfg = cfg_from_env(Config(alert_mode="console"), dotenv_path="/no/such/.env")
    assert cfg.alert_mode == "console"
