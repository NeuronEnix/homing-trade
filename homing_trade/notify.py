import os
from abc import ABC, abstractmethod


class Notifier(ABC):
    @abstractmethod
    def notify(self, level: str, title: str, message: str) -> None:
        raise NotImplementedError


class NullNotifier(Notifier):
    def notify(self, level, title, message):
        pass


class ConsoleNotifier(Notifier):
    def notify(self, level, title, message):
        print(f"[{level.upper()}] {title}: {message}")


class FileNotifier(Notifier):
    def __init__(self, path):
        self.path = path

    def notify(self, level, title, message):
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"{level}\t{title}\t{message}\n")


def _requests_poster(url, payload):
    import requests
    requests.post(url, json=payload, timeout=10)


class WebhookNotifier(Notifier):
    def __init__(self, url, poster=None):
        self.url = url
        self._poster = poster or _requests_poster

    def notify(self, level, title, message):
        try:
            self._poster(self.url, {"level": level, "title": title, "message": message})
        except Exception:
            pass  # alerts must never crash the bot


class TelegramNotifier(Notifier):
    """Sends alerts to a Telegram chat via the Bot API. Token + chat_id come from the
    environment (never hardcoded). Post errors are swallowed — alerts never crash the bot."""

    API = "https://api.telegram.org/bot{token}/sendMessage"
    _ICON = {"trade": "💱", "info": "ℹ️", "warn": "⚠️", "error": "🚨"}

    def __init__(self, token, chat_id, poster=None):
        self.token = token
        self.chat_id = chat_id
        self._poster = poster or _requests_poster

    def notify(self, level, title, message):
        text = f"{self._ICON.get(level, '•')} {title}\n{message}"
        try:
            self._poster(self.API.format(token=self.token),
                         {"chat_id": self.chat_id, "text": text})
        except Exception:
            pass  # alerts must never crash the bot


class DiscordNotifier(Notifier):
    """Sends alerts to a Discord channel via an incoming webhook URL (no bot token needed).
    The URL comes from the environment. Post errors are swallowed — alerts never crash the bot."""

    _ICON = {"trade": "💱", "info": "ℹ️", "warn": "⚠️", "error": "🚨"}

    def __init__(self, webhook_url, poster=None):
        self.webhook_url = webhook_url
        self._poster = poster or _requests_poster

    def notify(self, level, title, message):
        text = f"{self._ICON.get(level, '•')} **{title}**\n{message}"
        try:
            self._poster(self.webhook_url, {"content": text})
        except Exception:
            pass  # alerts must never crash the bot


def build_notifier(cfg):
    mode = getattr(cfg, "alert_mode", "console")
    if mode == "null":
        return NullNotifier()
    if mode == "file":
        return FileNotifier(cfg.alert_log_path)
    if mode == "webhook":
        return WebhookNotifier(cfg.webhook_url)
    if mode == "discord":
        return DiscordNotifier(os.environ.get(cfg.discord_webhook_env, ""))
    if mode == "telegram":
        return TelegramNotifier(os.environ.get(cfg.telegram_token_env, ""),
                                os.environ.get(cfg.telegram_chat_id_env, ""))
    return ConsoleNotifier()
