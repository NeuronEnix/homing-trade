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


def build_notifier(cfg):
    mode = getattr(cfg, "alert_mode", "console")
    if mode == "null":
        return NullNotifier()
    if mode == "file":
        return FileNotifier(cfg.alert_log_path)
    if mode == "webhook":
        return WebhookNotifier(cfg.webhook_url)
    return ConsoleNotifier()
