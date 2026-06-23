"""Two-way Discord comms channel — agent <-> human — separate from trade alerts.

OUTBOUND (works with just a webhook): post() sends a message to COMMS_WEBHOOK_URL. Used to
ping you with actionable items / progress during long or background tasks.

INBOUND (needs a BOT TOKEN — webhooks are write-only): read() pulls new messages from the
channel via the Discord REST API. It needs DISCORD_BOT_TOKEN (a bot added to your server with
"Read Message History" + the "Message Content Intent" enabled) and COMMS_CHANNEL_ID. Until
those are set, read() returns [] (inbound disabled) — outbound still works.

All values are read from `.env` (gitignored). Network helpers are injectable for tests.
"""
import os

WEBHOOK_ENV = "COMMS_WEBHOOK_URL"
BOT_TOKEN_ENV = "DISCORD_BOT_TOKEN"
CHANNEL_ID_ENV = "COMMS_CHANNEL_ID"


def _env(name, dotenv_path):
    from homing_trade.dotenv import load_dotenv
    load_dotenv(dotenv_path)
    return os.environ.get(name, "")


def _bot_post(text, token, channel_id, sender=None):
    """Send via the bot user (REST, `Authorization: Bot <token>`) — same identity that reads
    replies. Returns True on success, False on any error (never raises)."""
    try:
        if sender is None:
            import requests
            def sender(cid, tok, payload):
                r = requests.post(f"https://discord.com/api/v10/channels/{cid}/messages",
                                  headers={"Authorization": f"Bot {tok}"}, json=payload, timeout=10)
                r.raise_for_status()
        sender(channel_id, token, {"content": text[:1900]})
        return True
    except Exception:
        return False


def post(text, *, webhook_url=None, dotenv_path=".env", poster=None, sender=None):
    """Send a message to the comms channel. Returns True on success. Never raises.

    Prefers the BOT user (one identity for both directions) when a token + channel id are
    configured and the caller did not pass an explicit `webhook_url`; otherwise falls back to the
    webhook. Passing `webhook_url=` explicitly forces the webhook (back-compat for callers/tests)."""
    if webhook_url is None:
        token, channel_id = _env(BOT_TOKEN_ENV, dotenv_path), _env(CHANNEL_ID_ENV, dotenv_path)
        if token and channel_id:
            return _bot_post(text, token, channel_id, sender=sender)
    url = webhook_url if webhook_url is not None else _env(WEBHOOK_ENV, dotenv_path)
    if not url:
        return False
    try:
        if poster is None:
            import requests
            poster = lambda u, j: requests.post(u, json=j, timeout=10)
        poster(url, {"content": text[:1900]})
        return True
    except Exception:
        return False


def read(after_id=None, *, token=None, channel_id=None, limit=20, dotenv_path=".env", fetcher=None):
    """Return new human messages from the comms channel, oldest-first:
    [{"id","author","content"}]. Empty list if inbound isn't configured or on any error.
    Bot/webhook messages (including our own) are filtered out."""
    token = token or _env(BOT_TOKEN_ENV, dotenv_path)
    channel_id = channel_id or _env(CHANNEL_ID_ENV, dotenv_path)
    if not token or not channel_id:
        return []  # inbound disabled — no bot token / channel id
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages?limit={limit}"
    if after_id:
        url += f"&after={after_id}"
    try:
        if fetcher is None:
            import requests
            def fetcher(u, headers):
                r = requests.get(u, headers=headers, timeout=10)
                r.raise_for_status()
                return r.json()
        msgs = fetcher(url, {"Authorization": f"Bot {token}"})
        out = []
        for m in reversed(msgs):  # API returns newest-first; we want oldest-first
            if m.get("author", {}).get("bot"):
                continue
            out.append({"id": m["id"], "author": m.get("author", {}).get("username", ""),
                        "content": m.get("content", "")})
        return out
    except Exception:
        return []


def inbound_enabled(*, dotenv_path=".env"):
    return bool(_env(BOT_TOKEN_ENV, dotenv_path) and _env(CHANNEL_ID_ENV, dotenv_path))
