import json
import threading
from homing_trade.daemon import run_daemon
from homing_trade.config import Config


class _Rec:
    def __init__(self):
        self.events = []
    def notify(self, level, title, message):
        self.events.append((level, title, message))


def test_clean_run_emits_start_stop(tmp_path):
    sp = str(tmp_path / "status.json")
    rec = _Rec()
    res = run_daemon(Config(), notifier=rec, status_path=sp,
                     run_engine=lambda: None, sleeper=lambda s: None, now_fn=lambda: 0)
    assert res["restarts"] == 0
    titles = [t for _, t, _ in rec.events]
    msgs = [m for _, _, m in rec.events]
    assert "started" in msgs and "stopped" in msgs
    assert json.load(open(sp))["state"] == "stopped"


def test_restart_once_then_succeeds(tmp_path):
    calls = {"n": 0}
    def re():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return None
    rec = _Rec()
    res = run_daemon(Config(), notifier=rec, status_path=str(tmp_path / "s.json"),
                     run_engine=re, sleeper=lambda s: None, now_fn=lambda: 0)
    assert res["restarts"] == 1 and calls["n"] == 2
    assert any(level == "error" for level, _, _ in rec.events)


def test_max_restarts_gives_up(tmp_path):
    def always_boom():
        raise RuntimeError("always")
    rec = _Rec()
    res = run_daemon(Config(), notifier=rec, status_path=str(tmp_path / "s.json"),
                     run_engine=always_boom, max_restarts=3, sleeper=lambda s: None, now_fn=lambda: 0)
    assert res["restarts"] == 3
    assert res["last_error"] == "always"


def test_backoff_uses_configured_seconds(tmp_path):
    seen = []
    calls = {"n": 0}
    def re():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return None
    run_daemon(Config(daemon_backoff_seconds=7), notifier=_Rec(),
               status_path=str(tmp_path / "s.json"), run_engine=re,
               sleeper=lambda s: seen.append(s), now_fn=lambda: 0)
    assert seen == [7]                       # backed off exactly once, with the configured value


def test_stop_during_backoff_prevents_further_restart(tmp_path):
    # Simulate a SIGTERM arriving DURING the restart-backoff window: the backoff sleeper sets
    # the (interruptible) stop_event, so the supervisor loop must exit without another engine
    # run rather than restarting. This is exactly what the default stop_event.wait backoff buys.
    ev = threading.Event()
    calls = {"n": 0}
    def re():
        calls["n"] += 1
        raise RuntimeError("boom")           # would loop forever without the stop
    def backoff(_secs):
        ev.set()                             # signal arrives mid-backoff
    res = run_daemon(Config(), notifier=_Rec(), status_path=str(tmp_path / "s.json"),
                     run_engine=re, sleeper=backoff, now_fn=lambda: 0, stop_event=ev)
    assert calls["n"] == 1 and res["restarts"] == 1   # crashed once, stop cut off the restart


def test_status_file_records_restart_then_stopped(tmp_path):
    sp = str(tmp_path / "s.json")
    calls = {"n": 0}
    def re():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("kaboom")
        return None
    run_daemon(Config(), notifier=_Rec(), status_path=sp, run_engine=re,
               sleeper=lambda s: None, now_fn=lambda: 0)
    final = json.load(open(sp))
    assert final["state"] == "stopped" and final["restarts"] == 1
    assert final["last_error"] == "kaboom"   # last error is retained for diagnostics
