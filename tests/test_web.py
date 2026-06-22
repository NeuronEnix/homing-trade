import json
import threading
import time
import urllib.request

from homing_trade.feed import get_prices
from homing_trade.web import Controller, build_state, make_server
from homing_trade.engine import process_tick, _open_position, _drain_commands
from homing_trade.broker import Broker
from homing_trade.db import Database
from homing_trade.config import Config
from homing_trade.models import Candle, Signal
from homing_trade.skills.base import Strategy
import queue


def candles(n=30):
    return [Candle(open=100, high=101, low=99, close=100, volume=1, time=1000 + i * 60000)
            for i in range(n)]


class _N:
    def notify(self, *a, **k):
        pass


class AlwaysLong(Strategy):
    name = "ma_trend"
    def on_candle(self, candles, position):
        return Signal("LONG") if position is None else Signal("HOLD")


# --- live prices ---
def test_get_prices_filters_symbols():
    ticker = [{"market": "BTCUSDT", "last_price": "64000", "change_24_hour": "1.5"},
              {"market": "ETHUSDT", "last_price": "3400", "change_24_hour": "-2.0"}]
    out = get_prices(["BTCUSDT", "ETHUSDT", "NOPEUSDT"], fetcher=lambda u, p: ticker)
    assert out["BTCUSDT"]["last"] == 64000.0 and out["BTCUSDT"]["change"] == 1.5
    assert out["ETHUSDT"]["change"] == -2.0
    assert out["NOPEUSDT"] is None


# --- controller lifecycle ---
def _blocking_runner(cfg, *, notifier=None, should_stop, is_paused=None, sleeper, commands=None):
    while not should_stop():
        sleeper(0.01)


def test_controller_start_pause_resume_stop():
    c = Controller(Config(), runner=_blocking_runner, notifier=_N())
    assert c.status() == "stopped"
    c.start()
    time.sleep(0.05)
    assert c.status() == "running"
    c.pause(); assert c.status() == "paused"
    c.resume(); assert c.status() == "running"
    c.stop(); assert c.status() == "stopped"


# --- pause blocks new entries ---
def test_pause_blocks_new_open(tmp_path):
    cfg = Config(db_path=str(tmp_path / "p.db"))
    db = Database(cfg.db_path)
    db.ensure_strategy("ma_trend", 5000.0)
    process_tick(db, Broker(cfg.fee, cfg.slippage), [AlwaysLong()], candles(), cfg,
                 None, None, is_paused=lambda: True)
    assert db.get_open_position("ma_trend") is None   # paused -> no entry
    db.close()


# --- manual exit via command queue ---
def test_close_command_exits_trade(tmp_path):
    cfg = Config(db_path=str(tmp_path / "q.db"))
    db = Database(cfg.db_path)
    broker = Broker(cfg.fee, cfg.slippage)
    db.ensure_strategy("ma_trend", 5000.0)
    skill = AlwaysLong()
    _open_position(db, broker, skill, "LONG", candles()[-1], cfg, 1000)
    assert db.get_open_position("ma_trend") is not None
    q = queue.Queue(); q.put({"action": "close", "strategy": "ma_trend"})
    _drain_commands(db, broker, [skill], candles()[-1], q)
    assert db.get_open_position("ma_trend") is None   # exited
    db.close()


# --- state snapshot ---
def test_build_state_shape(tmp_path):
    cfg = Config(db_path=str(tmp_path / "s.db"))
    db = Database(cfg.db_path)
    db.ensure_strategy("grid", 5000.0)
    db.ensure_strategy("llm_claude_code", 5000.0)
    db.record_llm_response("llm_claude_code", 1000, "cli", "claude-opus-4-8", "HOLD", 0.6,
                           "saw chop", "sideways", "no edge", "{}", None)
    db.close()

    class _Ctrl:
        last_error = None
        def status(self): return "running"
    st = build_state(cfg, _Ctrl())
    assert st["status"] == "running"
    names = {s["name"] for s in st["strategies"]}
    assert {"grid", "llm_claude_code"} <= names
    ai = next(s for s in st["strategies"] if s["name"] == "llm_claude_code")
    assert ai["is_ai"] and ai["ai"]["observation"] == "saw chop"


# --- HTTP smoke ---
def test_http_server_serves_state(tmp_path):
    cfg = Config(db_path=str(tmp_path / "h.db"), web_port=0)
    Database(cfg.db_path).close()
    ctrl = Controller(cfg, runner=_blocking_runner, notifier=_N())
    server = make_server(cfg, ctrl)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True); t.start()
    try:
        body = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=5).read()
        data = json.loads(body)
        assert data["status"] == "stopped" and "strategies" in data
        html = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read()
        assert b"homing-trade" in html
    finally:
        server.shutdown()


# --- dashboard template extracted to web_assets/ ---
def test_dashboard_html_loaded_from_asset():
    from homing_trade.web import DASHBOARD_HTML
    assert len(DASHBOARD_HTML) > 1000
    assert "<!doctype html>" in DASHBOARD_HTML.lower()
    assert "homing-trade" in DASHBOARD_HTML and "/api/state" in DASHBOARD_HTML
