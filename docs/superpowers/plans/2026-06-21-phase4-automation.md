# Phase 4: Automation (daemon, alerts, guarded live trading) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Run the bot unattended (daemon with heartbeat/restart/alerts), notify on trades and lifecycle events, and ship a complete-but-guarded CoinDCX live-order adapter that is dry-run by default and never auto-armed.

**Architecture:** Additive, default-off modules. The default daemon runs the *paper* engine; the live adapter is standalone and not imported by the engine.

**Tech Stack:** Python 3.12, stdlib (`hmac`, `hashlib`, `json`, `signal`) + `requests` + `pytest`.

## Global Constraints

- Python 3.12. All new code additive and default-off; existing tests stay green.
- **No real orders by default:** `LiveBroker` defaults `dry_run=True` and makes NO network call in dry-run. No API keys in code/git (read from env). The paper engine never routes to `LiveBroker`.
- Currency INR; `data/` gitignored.
- Run tests via `cd /Users/krb/adoc2/rnd/homing-trade && ./.venv/bin/python -m pytest <path> -v`.
- Commit after each task; every commit message ends with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

### Task 1: Config additions

**Files:** Modify `homing_trade/config.py`; Test `tests/test_config_phase4.py`

**Interfaces:** new `Config` fields: `alert_mode="console"`, `alert_log_path="data/alerts.log"`, `webhook_url=""`, `live_enabled=False`, `live_dry_run=True`, `coindcx_key_env="COINDCX_API_KEY"`, `coindcx_secret_env="COINDCX_API_SECRET"`, `daemon_status_path="data/daemon_status.json"`, `daemon_backoff_seconds=5`.

- [ ] **Step 1: failing test**
```python
# tests/test_config_phase4.py
from homing_trade.config import CONFIG


def test_phase4_defaults():
    assert CONFIG.alert_mode == "console"
    assert CONFIG.alert_log_path == "data/alerts.log"
    assert CONFIG.webhook_url == ""
    assert CONFIG.live_enabled is False
    assert CONFIG.live_dry_run is True
    assert CONFIG.coindcx_key_env == "COINDCX_API_KEY"
    assert CONFIG.coindcx_secret_env == "COINDCX_API_SECRET"
    assert CONFIG.daemon_status_path == "data/daemon_status.json"
    assert CONFIG.daemon_backoff_seconds == 5
```
- [ ] **Step 2: run → FAIL** (`AttributeError`).
- [ ] **Step 3: add fields** to the `Config` dataclass (after the Phase-3 fields, before `CONFIG = Config()`):
```python
    alert_mode: str = "console"          # "console" | "file" | "webhook" | "null"
    alert_log_path: str = "data/alerts.log"
    webhook_url: str = ""
    live_enabled: bool = False
    live_dry_run: bool = True
    coindcx_key_env: str = "COINDCX_API_KEY"
    coindcx_secret_env: str = "COINDCX_API_SECRET"
    daemon_status_path: str = "data/daemon_status.json"
    daemon_backoff_seconds: int = 5
```
- [ ] **Step 4: run → PASS**; then full suite → all pass.
- [ ] **Step 5: commit**
```bash
git add homing_trade/config.py tests/test_config_phase4.py
git commit -m "feat: Phase 4 config fields (alerts, daemon, live-trading guards)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Notifiers

**Files:** Create `homing_trade/notify.py`; Test `tests/test_notify.py`

**Interfaces:** `Notifier` ABC (`notify(level,title,message)`); `NullNotifier`, `ConsoleNotifier`, `FileNotifier(path)`, `WebhookNotifier(url, poster=None)`; `build_notifier(cfg) -> Notifier`.

- [ ] **Step 1: failing test**
```python
# tests/test_notify.py
from homing_trade.notify import (Notifier, NullNotifier, ConsoleNotifier, FileNotifier,
                                WebhookNotifier, build_notifier)
from homing_trade.config import Config


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
```
- [ ] **Step 2: run → FAIL** (ModuleNotFoundError).
- [ ] **Step 3: implement**
```python
# homing_trade/notify.py
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
```
- [ ] **Step 4: run → PASS (6)**; full suite → all pass.
- [ ] **Step 5: commit**
```bash
git add homing_trade/notify.py tests/test_notify.py
git commit -m "feat: pluggable notifiers (console/file/webhook/null)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: trades_after + engine run() notifier hook

**Files:** Modify `homing_trade/db.py`, `homing_trade/engine.py`; Test `tests/test_engine_notify.py`

**Interfaces:** `Database.trades_after(last_id) -> list[dict]` (id > last_id, oldest-first). `engine.run(..., notifier=None)` — when given, emits one `notify("trade", ...)` per new trade after each tick; default None = unchanged behaviour.

- [ ] **Step 1: failing test**
```python
# tests/test_engine_notify.py
import homing_trade.engine as eng
from homing_trade.engine import run
from homing_trade.db import Database
from homing_trade.config import Config
from homing_trade.skills.base import Strategy
from homing_trade.models import Candle, Signal


class _RecNotifier:
    def __init__(self):
        self.events = []
    def notify(self, level, title, message):
        self.events.append((level, title, message))


class AlwaysLong(Strategy):
    name = "ma_trend"
    def on_candle(self, candles, position):
        return Signal("LONG") if position is None else Signal("HOLD")


def candles():
    return [Candle(open=100, high=101, low=99, close=100, volume=1, time=1000 + i * 60000)
            for i in range(30)]


def test_trades_after(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.record_trade("ma_trend", 1, "LONG", "OPEN", 100, 1, 0.1, -0.1, 1000)
    db.record_trade("ma_trend", 1, "LONG", "CLOSE", 110, 1, 0.1, 9.9, 2000)
    after = db.trades_after(1)  # ids start at 1; first row has id 1 -> only the 2nd returned
    assert len(after) == 1 and after[0]["action"] == "CLOSE"
    assert [t["id"] for t in db.trades_after(0)] == [1, 2]  # oldest-first
    db.close()


def test_run_emits_trade_alerts(tmp_path, monkeypatch):
    monkeypatch.setitem(eng._SKILL_FACTORY, "ma_trend", AlwaysLong)
    cfg = Config(db_path=str(tmp_path / "n.db"), enabled_skills=["ma_trend"])
    raw = [{"open": c.open, "high": c.high, "low": c.low, "close": c.close,
            "volume": c.volume, "time": c.time} for c in candles()]
    notifier = _RecNotifier()
    run(cfg, fetcher=lambda url, params: raw, max_ticks=1, sleeper=lambda s: None, notifier=notifier)
    trade_events = [e for e in notifier.events if e[0] == "trade"]
    assert len(trade_events) >= 1
    assert "ma_trend" in trade_events[0][1]
```
- [ ] **Step 2: run → FAIL** (`AttributeError: trades_after` / no notifier param).
- [ ] **Step 3: implement**

Add to `homing_trade/db.py` `Database`:
```python
    def trades_after(self, last_id):
        rows = self.conn.execute(
            "SELECT id, strategy, side, action, price, size, pnl FROM trades WHERE id>? ORDER BY id ASC",
            (last_id,)).fetchall()
        return [dict(r) for r in rows]
```

In `homing_trade/engine.py`, change `run`'s signature to add `notifier=None`:
```python
def run(cfg=CONFIG, *, fetcher=None, max_ticks=None, sleeper=None, notifier=None):
```
Just after `skills = build_skills(cfg.enabled_skills, cfg)` (and the ensure_strategy loop), seed the last-alerted id:
```python
    last_alert_id = 0
    if notifier is not None:
        _row = db.conn.execute("SELECT MAX(id) AS m FROM trades").fetchone()
        last_alert_id = _row["m"] or 0
```
Inside the loop, AFTER `process_tick(...)` and `db.set_state(...)` (still inside `if candles:`), add:
```python
                if notifier is not None:
                    for t in db.trades_after(last_alert_id):
                        notifier.notify("trade", f"{t['strategy']} {t['action']}",
                                        f"{t['side']} {t['size']:.6f} @ {t['price']:.2f} pnl={t['pnl']:.2f}")
                        last_alert_id = t["id"]
```
Change nothing else in `run`/`process_tick`.

- [ ] **Step 4: run → PASS**; full suite → all pass (notifier default None keeps prior tests green).
- [ ] **Step 5: commit**
```bash
git add homing_trade/db.py homing_trade/engine.py tests/test_engine_notify.py
git commit -m "feat: trades_after + engine run() per-trade alert hook (default off)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Daemon / supervisor

**Files:** Create `homing_trade/daemon.py`; Test `tests/test_daemon.py`

**Interfaces:** `run_daemon(cfg=CONFIG, *, notifier=None, status_path=None, run_engine=None, max_restarts=None, sleeper=None, now_fn=None) -> dict`.

- [ ] **Step 1: failing test**
```python
# tests/test_daemon.py
import json
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
```
- [ ] **Step 2: run → FAIL** (ModuleNotFoundError).
- [ ] **Step 3: implement**
```python
# homing_trade/daemon.py
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


if __name__ == "__main__":
    run_daemon()
```
- [ ] **Step 4: run → PASS (3)**; full suite → all pass.
- [ ] **Step 5: commit**
```bash
git add homing_trade/daemon.py tests/test_daemon.py
git commit -m "feat: daemon supervisor (signals, heartbeat, restart, lifecycle alerts)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Guarded live-trading adapter

**Files:** Create `homing_trade/live_broker.py`; Test `tests/test_live_broker.py`

**Interfaces:** `sign(secret, body) -> hexdigest`; `build_order_payload(market, side, order_type, quantity, price, now_ms) -> dict`; `LiveBroker(api_key=None, api_secret=None, dry_run=True, base_url="https://api.coindcx.com", poster=None)` with `place_order(...)` and `from_signal(signal, market, quantity, price, now_ms, order_type="market_order")`.

- [ ] **Step 1: failing test**
```python
# tests/test_live_broker.py
import hashlib
import hmac
import json
import pytest
from homing_trade.live_broker import sign, build_order_payload, LiveBroker
from homing_trade.models import Signal


def test_sign_matches_hmac_sha256_of_compact_json():
    body = {"a": 1, "b": 2}
    expected = hmac.new(b"secret",
                        json.dumps(body, separators=(",", ":")).encode("utf-8"),
                        hashlib.sha256).hexdigest()
    assert sign("secret", body) == expected


def test_build_order_payload_shape():
    p = build_order_payload("B-BTC_USDT", "buy", "market_order", 0.001, 0.0, 1717000000000)
    assert p["market"] == "B-BTC_USDT" and p["side"] == "buy"
    assert p["order_type"] == "market_order" and p["total_quantity"] == 0.001
    assert p["timestamp"] == 1717000000000


def test_dry_run_makes_no_network_call():
    def boom(url, headers, body):
        raise AssertionError("dry-run must NOT call the network")
    lb = LiveBroker(dry_run=True, poster=boom)
    res = lb.place_order("BTCINR", "buy", "market_order", 0.001, 0.0, 1)
    assert res["status"] == "dry_run"
    assert res["payload"]["market"] == "BTCINR"


def test_live_path_signs_and_posts():
    captured = {}
    def poster(url, headers, body):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = body
        return {"status": "ok", "id": "abc"}
    lb = LiveBroker(api_key="K", api_secret="S", dry_run=False, poster=poster)
    res = lb.place_order("BTCINR", "buy", "market_order", 0.001, 0.0, 1)
    assert res == {"status": "ok", "id": "abc"}
    assert captured["headers"]["X-AUTH-APIKEY"] == "K"
    assert captured["headers"]["X-AUTH-SIGNATURE"] == sign("S", captured["body"])
    assert captured["url"].endswith("/exchange/v1/orders/create")


def test_live_path_requires_keys():
    lb = LiveBroker(dry_run=False, poster=lambda u, h, b: {})
    with pytest.raises(ValueError):
        lb.place_order("BTCINR", "buy", "market_order", 0.001, 0.0, 1)


def test_from_signal_maps_actions():
    lb = LiveBroker(dry_run=True)
    assert lb.from_signal(Signal("LONG"), "BTCINR", 0.001, 0.0, 1)["payload"]["side"] == "buy"
    assert lb.from_signal(Signal("CLOSE"), "BTCINR", 0.001, 0.0, 1)["payload"]["side"] == "sell"
    assert lb.from_signal(Signal("HOLD"), "BTCINR", 0.001, 0.0, 1)["status"] == "noop"
```
- [ ] **Step 2: run → FAIL** (ModuleNotFoundError).
- [ ] **Step 3: implement**
```python
# homing_trade/live_broker.py
import hashlib
import hmac
import json

ORDER_PATH = "/exchange/v1/orders/create"


def sign(secret, body):
    payload = json.dumps(body, separators=(",", ":"))
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def build_order_payload(market, side, order_type, quantity, price, now_ms):
    return {
        "market": market,
        "side": side,                  # "buy" | "sell"
        "order_type": order_type,      # "limit_order" | "market_order"
        "price_per_unit": price,
        "total_quantity": quantity,
        "timestamp": now_ms,
    }


def _requests_poster(url, headers, body):
    import requests
    resp = requests.post(url, headers=headers,
                         data=json.dumps(body, separators=(",", ":")), timeout=10)
    resp.raise_for_status()
    return resp.json()


class LiveBroker:
    """Guarded CoinDCX order adapter. dry_run=True (default) NEVER makes a network call —
    it returns a simulated ack. Real orders require dry_run=False AND api_key/api_secret
    (read from env by the caller, never hardcoded)."""

    def __init__(self, api_key=None, api_secret=None, dry_run=True,
                 base_url="https://api.coindcx.com", poster=None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.dry_run = dry_run
        self.base_url = base_url
        self._poster = poster or _requests_poster

    def place_order(self, market, side, order_type, quantity, price, now_ms):
        body = build_order_payload(market, side, order_type, quantity, price, now_ms)
        if self.dry_run:
            return {"status": "dry_run", "payload": body}
        if not self.api_key or not self.api_secret:
            raise ValueError("live order requires api_key and api_secret "
                             "(set COINDCX_API_KEY / COINDCX_API_SECRET)")
        headers = {
            "Content-Type": "application/json",
            "X-AUTH-APIKEY": self.api_key,
            "X-AUTH-SIGNATURE": sign(self.api_secret, body),
        }
        return self._poster(f"{self.base_url}{ORDER_PATH}", headers, body)

    def from_signal(self, signal, market, quantity, price, now_ms, order_type="market_order"):
        if signal.action == "LONG":
            return self.place_order(market, "buy", order_type, quantity, price, now_ms)
        if signal.action in ("CLOSE", "SHORT"):
            return self.place_order(market, "sell", order_type, quantity, price, now_ms)
        return {"status": "noop", "action": signal.action}
```
- [ ] **Step 4: run → PASS (6)**; full suite → all pass.
- [ ] **Step 5: commit**
```bash
git add homing_trade/live_broker.py tests/test_live_broker.py
git commit -m "feat: guarded CoinDCX live-order adapter (dry-run default, HMAC-signed)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:** notifiers (§3)→T2; per-trade hook + trades_after (§4)→T3; daemon (§5)→T4; live adapter (§6)→T5; config (§7)→T1; testing (§9) per task. ✅
**Placeholder scan:** T3 gives precise edits against the existing `run`/`db`; all other steps full code. ✅
**Type consistency:** `Notifier.notify(level,title,message)` used by daemon + engine hook + tests. `build_order_payload`/`sign` consumed by `LiveBroker`. `trades_after(last_id)` consumed by the engine hook. `LiveBroker(dry_run=True default)` — the safety default. ✅
**Safety review:** LiveBroker dry-run path returns before any network use and is the constructor default; live path requires keys; the paper engine never imports `live_broker`; the daemon CLI runs the paper engine only. ✅
