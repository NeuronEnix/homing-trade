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


# --- Phase 3 #8: bot-first send (the webhook becomes optional once the bot can post) ---
def test_post_prefers_bot_when_token_present(monkeypatch):
    vals = {"DISCORD_BOT_TOKEN": "tok", "COMMS_CHANNEL_ID": "chan", "COMMS_WEBHOOK_URL": "http://hook"}
    monkeypatch.setattr(comms, "_env", lambda name, path: vals.get(name, ""))
    sent = {}
    ok = comms.post("hi there", sender=lambda cid, tok, payload: sent.update(
        cid=cid, tok=tok, content=payload["content"]))
    assert ok and sent == {"cid": "chan", "tok": "tok", "content": "hi there"}  # bot, not webhook


def test_post_falls_back_to_webhook_when_no_bot(monkeypatch):
    monkeypatch.setattr(comms, "_env", lambda name, path: {"COMMS_WEBHOOK_URL": "http://hook"}.get(name, ""))
    calls = []
    assert comms.post("hi", poster=lambda u, j: calls.append(u)) is True
    assert calls == ["http://hook"]


def test_explicit_webhook_url_forces_webhook_even_with_bot(monkeypatch):
    # back-compat: an explicit webhook_url is honored regardless of an available bot token
    monkeypatch.setattr(comms, "_env",
                        lambda name, path: {"DISCORD_BOT_TOKEN": "tok", "COMMS_CHANNEL_ID": "chan"}.get(name, ""))
    poster_calls, sender_calls = [], []
    ok = comms.post("hi", webhook_url="http://explicit",
                    poster=lambda u, j: poster_calls.append(u),
                    sender=lambda *a: sender_calls.append(a))
    assert ok and poster_calls == ["http://explicit"] and sender_calls == []


def test_bot_post_swallows_errors():
    def boom(cid, tok, payload):
        raise RuntimeError("403")
    assert comms._bot_post("x", "tok", "chan", sender=boom) is False
