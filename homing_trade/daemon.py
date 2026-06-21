import json
import os
import signal
import time
from homing_trade.config import CONFIG
from homing_trade.notify import build_notifier
from homing_trade.engine import run as engine_run


def _write_status(path, state, restarts, last_error, ts):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"state": state, "restarts": restarts, "last_error": last_error, "ts": ts}, f)


def run_daemon(cfg=CONFIG, *, notifier=None, status_path=None, run_engine=None,
               max_restarts=None, sleeper=None, now_fn=None):
    notifier = notifier or build_notifier(cfg)
    status_path = status_path or cfg.daemon_status_path
    run_engine = run_engine or (lambda: engine_run(cfg, notifier=notifier))
    sleeper = sleeper or time.sleep
    now_fn = now_fn or (lambda: int(time.time() * 1000))

    stop = {"flag": False}

    def _handler(signum, frame):
        stop["flag"] = True

    try:
        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)
    except (ValueError, OSError):
        pass  # not main thread (e.g. tests) — signals optional

    notifier.notify("info", "daemon", "started")
    _write_status(status_path, "running", 0, None, now_fn())
    restarts = 0
    last_error = None
    while not stop["flag"]:
        try:
            run_engine()
            break  # engine returned normally (e.g. max_ticks reached) — done
        except Exception as exc:
            restarts += 1
            last_error = str(exc)
            notifier.notify("error", "daemon", f"engine crashed (restart {restarts}): {exc}")
            _write_status(status_path, "restarting", restarts, last_error, now_fn())
            if max_restarts is not None and restarts >= max_restarts:
                notifier.notify("error", "daemon", "max restarts reached, giving up")
                break
            sleeper(cfg.daemon_backoff_seconds)
    notifier.notify("info", "daemon", "stopped")
    _write_status(status_path, "stopped", restarts, last_error, now_fn())
    return {"restarts": restarts, "last_error": last_error}


def cfg_from_env(cfg=CONFIG, *, dotenv_path=".env"):
    """Apply `.env` / environment overrides (leverage, daily limits, kill switch, alert
    channel). Delegates to config.from_env so all HOMING_* vars are honored."""
    from homing_trade.config import from_env
    return from_env(cfg, dotenv_path=dotenv_path)


if __name__ == "__main__":
    run_daemon(cfg_from_env())
