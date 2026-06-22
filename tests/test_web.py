import json
import threading
import time
import urllib.request

from homing_trade.feed import get_prices
from homing_trade.web import Controller, build_state, make_server
from homing_trade.engine import process_tick, _open_position, _drain_commands
from homing_trade.broker import Broker
from homing_trade.db import Database
from homing_trade.repository import Repository
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


def test_build_state_leaderboard_metrics(tmp_path):
    cfg = Config(db_path=str(tmp_path / "lb.db"))
    db = Database(cfg.db_path)
    db.ensure_strategy("grid", 5000.0)
    db.ensure_strategy("ma", 5000.0)
    # grid: two wins (+100, +60), one loss (-40); equity dips then recovers higher
    db.record_trade("grid", 1, "LONG", "CLOSE", 110, 1, 0.1, 100.0, 1000)
    db.record_trade("grid", 2, "LONG", "CLOSE", 90, 1, 0.1, -40.0, 2000)
    db.record_trade("grid", 3, "LONG", "CLOSE", 120, 1, 0.1, 60.0, 3000)
    db.record_equity("grid", 5100.0, 1000)
    db.record_equity("grid", 5060.0, 2000)   # dip -> drawdown
    db.record_equity("grid", 5120.0, 3000)
    # two decisions: an earlier HOLD then a later LONG -> last_action must pick the newest
    db.log_decision("grid", 2000, 1999, "HOLD", 0.0, "wait", {}, taken_action="HOLD")
    db.log_decision("grid", 3000, 2999, "LONG", 0.7, "breakout", {}, taken_action="LONG")
    # ma: one loss, ends lower -> ranks below grid
    db.record_trade("ma", 4, "LONG", "CLOSE", 95, 1, 0.1, -50.0, 1000)
    db.record_equity("ma", 4950.0, 1000)
    db.close()

    class _Ctrl:
        last_error = None
        def status(self): return "running"
    st = build_state(cfg, _Ctrl())
    by = {s["name"]: s for s in st["strategies"]}
    g = by["grid"]
    assert g["rank"] == 1 and by["ma"]["rank"] == 2          # higher equity ranks first
    assert st["strategies"][0]["name"] == "grid"             # list is sorted by rank
    assert g["trades"] == 3
    assert round(g["win_rate"], 4) == round(2 / 3, 4)
    assert g["profit_factor"] == 160.0 / 40.0                # gross profit 160 / gross loss 40
    assert g["max_drawdown"] > 0                             # 5100 -> 5060 dip
    assert g["realized_pnl"] == 120.0
    assert g["last_action"] == "LONG"                        # most recent decision's action
    assert g["equity_curve"][-1] == 5120.0 and len(g["equity_curve"]) == 3


def test_build_state_brain_log(tmp_path):
    cfg = Config(db_path=str(tmp_path / "bl.db"))
    db = Database(cfg.db_path)
    db.ensure_strategy("llm_claude_code", 5000.0)
    db.ensure_strategy("llm_anthropic", 5000.0)
    db.record_llm_response("llm_claude_code", 1000, "cli", "claude-opus-4-8", "HOLD", 0.6,
                           "saw chop", "sideways", "no edge", "{}", None, next_check_in_sec=900)
    db.record_llm_response("llm_anthropic", 2000, "api", "claude-3", None, None,
                           None, None, None, "{}", "rate limited")   # an error response
    db.close()

    class _Ctrl:
        last_error = None
        def status(self): return "running"
    st = build_state(cfg, _Ctrl())
    bl = st["brain_log"]
    assert len(bl) == 2
    assert bl[0]["strategy"] == "llm_anthropic" and bl[0]["error"] == "rate limited"  # newest first
    cc = next(b for b in bl if b["strategy"] == "llm_claude_code")
    assert cc["observation"] == "saw chop" and cc["prediction"] == "sideways"
    assert cc["rationale"] == "no edge" and cc["next_check_in_sec"] == 900
    assert "raw" not in cc                      # bulky envelope excluded
    json.dumps(st)                              # JSON-safe


def test_build_state_regime_and_exit_breakdown(tmp_path):
    cfg = Config(db_path=str(tmp_path / "rb.db"))
    db = Database(cfg.db_path)
    db.ensure_strategy("ma", 5000.0)
    # one winning trend_up trade (signal exit), one losing chop trade (stop exit)
    db.record_trade("ma", 1, "LONG", "OPEN", 100, 1, 0.1, -0.1, 1000, regime_at_entry="trend_up")
    db.record_trade("ma", 1, "LONG", "CLOSE", 110, 1, 0.1, 9.9, 2000, exit_reason="signal")
    db.record_trade("ma", 2, "LONG", "OPEN", 100, 1, 0.1, -0.1, 3000, regime_at_entry="chop")
    db.record_trade("ma", 2, "LONG", "CLOSE", 95, 1, 0.1, -5.1, 4000, exit_reason="stop")
    db.rebuild_trade_outcomes()
    db.close()

    class _Ctrl:
        last_error = None
        def status(self): return "running"
    st = build_state(cfg, _Ctrl())
    rb = st["regime_breakdown"]
    assert rb["trend_up"]["trades"] == 1 and rb["trend_up"]["win_rate"] == 1.0
    assert rb["chop"]["trades"] == 1 and rb["chop"]["win_rate"] == 0.0
    eb = st["exit_breakdown"]
    assert set(eb) == {"signal", "stop"} and eb["stop"]["trades"] == 1
    json.dumps(st)                                          # JSON-safe


def test_process_tick_rebuilds_trade_outcomes(tmp_path):
    # The engine must keep trade_outcomes fresh: open then close a position across two ticks
    # and assert the denormalized outcome row appears (this is what the breakdown panels read).
    class _OpenThenClose:
        name = "ma"
        def __init__(self): self.n = 0
        def on_candle(self, candles, position):
            self.n += 1
            act = "LONG" if position is None else "CLOSE"
            return Signal(action=act, confidence=1.0, reason="x", indicators={})
    cfg = Config(db_path=str(tmp_path / "to.db"))
    db = Database(cfg.db_path); db.ensure_strategy("ma", 5000.0)
    repo = Repository.open(cfg.db_path)
    broker = Broker(cfg.fee, cfg.slippage)
    skill = _OpenThenClose()
    process_tick(repo, broker, [skill], candles(), cfg)     # opens
    process_tick(repo, broker, [skill], candles(), cfg)     # closes -> outcome row built
    outs = repo.trade_outcomes()
    repo.close(); db.close()
    assert len(outs) == 1 and outs[0]["strategy"] == "ma"


def test_build_state_profit_factor_none_is_json_safe(tmp_path):
    # A strategy with only winning trades -> profit_factor is undefined; must serialize as null.
    cfg = Config(db_path=str(tmp_path / "pf.db"))
    db = Database(cfg.db_path)
    db.ensure_strategy("grid", 5000.0)
    db.record_trade("grid", 1, "LONG", "CLOSE", 110, 1, 0.1, 50.0, 1000)  # only a win

    class _Ctrl:
        last_error = None
        def status(self): return "running"
    st = build_state(cfg, _Ctrl())
    db.close()
    g = next(s for s in st["strategies"] if s["name"] == "grid")
    assert g["profit_factor"] is None
    json.dumps(st)                                           # must not raise (no inf/nan)


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
