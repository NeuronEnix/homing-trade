from algotrading.notify import (Notifier, NullNotifier, ConsoleNotifier, FileNotifier,
                                WebhookNotifier, build_notifier)
from algotrading.config import Config


def test_null_notifier_no_op():
    NullNotifier().notify("info", "t", "m")  # must not raise


def test_console_notifier_prints(capsys):
    ConsoleNotifier().notify("trade", "ma_trend OPEN", "buy 1 @ 100")
    out = capsys.readouterr().out
    assert "TRADE" in out and "ma_trend OPEN" in out


def test_file_notifier_appends(tmp_path):
    p = str(tmp_path / "a.log")
    n = FileNotifier(p)
    n.notify("info", "t1", "m1")
    n.notify("warn", "t2", "m2")
    lines = open(p, encoding="utf-8").read().splitlines()
    assert len(lines) == 2 and "t1" in lines[0] and "t2" in lines[1]


def test_webhook_posts_via_injected_poster():
    sent = []
    n = WebhookNotifier("http://hook", poster=lambda url, payload: sent.append((url, payload)))
    n.notify("error", "boom", "details")
    assert sent and sent[0][0] == "http://hook"
    assert sent[0][1] == {"level": "error", "title": "boom", "message": "details"}


def test_webhook_swallows_poster_error():
    def boom(url, payload):
        raise RuntimeError("network down")
    WebhookNotifier("http://hook", poster=boom).notify("info", "t", "m")  # must NOT raise


def test_build_notifier_modes(tmp_path):
    assert isinstance(build_notifier(Config(alert_mode="null")), NullNotifier)
    assert isinstance(build_notifier(Config(alert_mode="console")), ConsoleNotifier)
    assert isinstance(build_notifier(Config(alert_mode="file")), FileNotifier)
    assert isinstance(build_notifier(Config(alert_mode="webhook")), WebhookNotifier)
