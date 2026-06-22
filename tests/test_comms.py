from homing_trade import comms

MISSING = "/no/such/.env"


def test_post_uses_webhook_and_content():
    sent = {}
    def poster(url, payload):
        sent["url"] = url
        sent["payload"] = payload
    assert comms.post("hello", webhook_url="http://hook", poster=poster) is True
    assert sent["url"] == "http://hook"
    assert sent["payload"]["content"] == "hello"


def test_post_no_webhook_returns_false(monkeypatch):
    monkeypatch.delenv("COMMS_WEBHOOK_URL", raising=False)
    assert comms.post("x", webhook_url="", dotenv_path=MISSING) is False


def test_post_swallows_errors():
    def boom(url, payload):
        raise RuntimeError("network down")
    assert comms.post("x", webhook_url="http://hook", poster=boom) is False


def test_read_disabled_without_token(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("COMMS_CHANNEL_ID", raising=False)
    assert comms.read(dotenv_path=MISSING) == []


def test_read_parses_oldest_first_and_filters_bots():
    api = [  # Discord returns newest-first
        {"id": "3", "author": {"username": "krb", "bot": False}, "content": "do X"},
        {"id": "2", "author": {"username": "tradebot", "bot": True}, "content": "ignore"},
        {"id": "1", "author": {"username": "krb", "bot": False}, "content": "earlier"},
    ]
    out = comms.read(token="t", channel_id="c", fetcher=lambda u, h: api)
    assert [m["content"] for m in out] == ["earlier", "do X"]  # oldest-first, bot filtered
    assert out[0]["author"] == "krb" and out[0]["id"] == "1"


def test_read_swallows_errors():
    def boom(u, h):
        raise RuntimeError("401")
    assert comms.read(token="t", channel_id="c", fetcher=boom) == []
