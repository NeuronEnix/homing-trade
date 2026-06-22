import json
import threading
import time
import urllib.request
import urllib.error

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
def _blocking_runner(cfg, *, notifier=None, should_stop, is_paused=None, sleeper, commands=None,
                     is_disabled=None):
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
        disabled = set()
        def status(self): return "running"
    st = build_state(cfg, _Ctrl())
    assert st["status"] == "running"
    names = {s["name"] for s in st["strategies"]}
    assert {"grid", "llm_claude_code"} <= names
    ai = next(s for s in st["strategies"] if s["name"] == "llm_claude_code")
    assert ai["is_ai"] and ai["ai"]["observation"] == "saw chop"


def test_build_state_surfaces_per_ai_cost(tmp_path):
    # Phase 5 #4: the leaderboard item for an AI carries its tokens + $ from the cost_ledger.
    cfg = Config(db_path=str(tmp_path / "cost.db"))
    db = Database(cfg.db_path)
    db.ensure_strategy("llm_anthropic", 5000.0)
    db.record_cost("llm_anthropic", 1000, "claude-opus-4-8", "api", 100, 20, 0.003)
    db.record_cost("llm_anthropic", 2000, "claude-opus-4-8", "api", 50, 10, 0.0015)
    db.close()

    class _Ctrl:
        last_error = None
        disabled = set()
        def status(self): return "running"
    st = build_state(cfg, _Ctrl())
    ai = next(s for s in st["strategies"] if s["name"] == "llm_anthropic")
    assert ai["cost"]["calls"] == 2 and ai["cost"]["total_tokens"] == 180
    assert ai["cost"]["usd"] == 0.0045


def test_build_state_surfaces_signal_cache_freshness(tmp_path):
    # Phase 6 #6: build_state exposes per-source external-signal cache freshness.
    cfg = Config(db_path=str(tmp_path / "sig.db"))
    db = Database(cfg.db_path)
    db.upsert_signal("fng", "latest", 1000, {"value": 40}, 2000)
    db.upsert_signal("news", "headlines", 1500, [{"t": "x"}], 3000)
    db.close()

    class _Ctrl:
        last_error = None
        disabled = set()
        def status(self): return "running"
    st = build_state(cfg, _Ctrl())
    srcs = {s["source"] for s in st["signals"]}
    assert {"fng", "news"} <= srcs
    fng = next(s for s in st["signals"] if s["source"] == "fng")
    assert fng["fetched_at"] == 2000 and "age_sec" in fng and "stale" in fng


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
        disabled = set()
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
        disabled = set()
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
        disabled = set()
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
        disabled = set()
        def status(self): return "running"
    st = build_state(cfg, _Ctrl())
    db.close()
    g = next(s for s in st["strategies"] if s["name"] == "grid")
    assert g["profit_factor"] is None
    json.dumps(st)                                           # must not raise (no inf/nan)


def test_strategy_toggle_and_build_state_flag(tmp_path):
    cfg = Config(db_path=str(tmp_path / "tog.db"))
    db = Database(cfg.db_path); db.ensure_strategy("ma", 5000.0); db.close()
    ctrl = Controller(cfg, runner=_blocking_runner, notifier=_N())
    ctrl.set_strategy_enabled("ma", False)
    assert "ma" in ctrl.disabled
    ma = next(s for s in build_state(cfg, ctrl)["strategies"] if s["name"] == "ma")
    assert ma["disabled"] is True
    ctrl.set_strategy_enabled("ma", True)                    # re-enable
    ma = next(s for s in build_state(cfg, ctrl)["strategies"] if s["name"] == "ma")
    assert ma["disabled"] is False


def test_http_toggle_endpoint(tmp_path):
    cfg = Config(db_path=str(tmp_path / "h2.db"), web_port=0)
    db = Database(cfg.db_path); db.ensure_strategy("ma", 5000.0); db.close()
    ctrl = Controller(cfg, runner=_blocking_runner, notifier=_N())
    server = make_server(cfg, ctrl)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True); t.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/toggle",
            data=json.dumps({"strategy": "ma", "enabled": False}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        resp = json.loads(urllib.request.urlopen(req, timeout=5).read())
        assert resp["ok"] and "ma" in resp["disabled"]
        assert "ma" in ctrl.disabled
    finally:
        server.shutdown()


# --- proposal queue (Phase-3 #4 / Phase-4 #7) ---
def test_build_state_surfaces_proposal_queue(tmp_path):
    cfg = Config(db_path=str(tmp_path / "pq.db"))
    db = Database(cfg.db_path)
    db.ensure_strategy("ma", 5000.0)
    db.create_proposal("ma", "playbook", {"version": "ma-v1", "rules": ["skip chop"]},
                       "stops too tight in chop", 1000)
    db.close()

    class _Ctrl:
        last_error = None
        disabled = set()
        def status(self): return "running"
    st = build_state(cfg, _Ctrl())
    props = st["proposals"]
    assert len(props) == 1
    p = props[0]
    assert p["kind"] == "playbook" and p["strategy"] == "ma" and p["status"] == "pending"
    assert p["rationale"] == "stops too tight in chop"
    assert p["payload"]["rules"] == ["skip chop"]       # parsed for the UI
    json.dumps(st)                                       # JSON-safe


def test_controller_approve_applies_playbook(tmp_path):
    cfg = Config(db_path=str(tmp_path / "ap.db"))
    db = Database(cfg.db_path); db.ensure_strategy("ma", 5000.0)
    pid = db.create_proposal("ma", "playbook", {"version": "ma-v1", "rules": ["trend only"]},
                             "why", 1000)
    db.close()
    ctrl = Controller(cfg, runner=_blocking_runner, notifier=_N())
    res = ctrl.decide_proposal(pid, "approve")
    assert res["ok"] and res["status"] == "applied" and res["result"] == "ma-v1"
    repo = Repository.open(cfg.db_path)
    assert repo.latest_playbook("ma")["version"] == "ma-v1"      # published + active
    assert repo.get_proposal(pid)["applied_ts"] is not None
    repo.close()


def test_controller_reject_changes_nothing(tmp_path):
    cfg = Config(db_path=str(tmp_path / "rj.db"))
    db = Database(cfg.db_path); db.ensure_strategy("ma", 5000.0)
    pid = db.create_proposal("ma", "playbook", {"version": "ma-v1", "rules": ["x"]}, "why", 1000)
    db.close()
    ctrl = Controller(cfg, runner=_blocking_runner, notifier=_N())
    res = ctrl.decide_proposal(pid, "reject")
    assert res["ok"] and res["status"] == "rejected"
    repo = Repository.open(cfg.db_path)
    assert repo.latest_playbook("ma") is None                    # nothing published
    assert repo.get_proposal(pid)["status"] == "rejected"
    repo.close()


def test_controller_approve_param_is_approved_but_not_applied(tmp_path):
    # A param proposal is a legit approval, but apply isn't wired for params yet — the approval
    # must stand (status approved) while clearly reporting it wasn't auto-applied.
    cfg = Config(db_path=str(tmp_path / "pa.db"))
    db = Database(cfg.db_path); db.ensure_strategy("ma", 5000.0)
    pid = db.create_proposal("ma", "param", {"ema_period": 34}, "tune", 1000)
    db.close()
    ctrl = Controller(cfg, runner=_blocking_runner, notifier=_N())
    res = ctrl.decide_proposal(pid, "approve")
    assert res["ok"] and res["status"] == "approved" and res["applied"] is False and res["note"]
    repo = Repository.open(cfg.db_path)
    assert repo.get_proposal(pid)["status"] == "approved"
    assert repo.get_proposal(pid)["applied_ts"] is None
    repo.close()


def test_controller_decide_unknown_decision_rejected(tmp_path):
    cfg = Config(db_path=str(tmp_path / "ud.db"))
    Database(cfg.db_path).close()
    ctrl = Controller(cfg, runner=_blocking_runner, notifier=_N())
    assert ctrl.decide_proposal(1, "maybe")["ok"] is False


def test_controller_decide_nonexistent_and_nonpending(tmp_path):
    cfg = Config(db_path=str(tmp_path / "ne.db"))
    db = Database(cfg.db_path); db.ensure_strategy("ma", 5000.0)
    pid = db.create_proposal("ma", "playbook", {"version": "v1", "rules": ["x"]}, "w", 1000)
    db.close()
    ctrl = Controller(cfg, runner=_blocking_runner, notifier=_N())
    assert ctrl.decide_proposal(999, "approve")["ok"] is False        # nonexistent -> not pending
    ctrl.decide_proposal(pid, "reject")                                # decide it once
    assert ctrl.decide_proposal(pid, "reject")["status"] == "not_pending"  # re-reject is a no-op
    assert ctrl.decide_proposal(pid, "approve")["ok"] is False         # can't approve a rejected one


def test_dashboard_escapes_model_text_to_prevent_injection():
    # The AI's rationale/rules are untrusted text rendered into innerHTML; the dashboard must
    # escape them (esc helper) so a crafted string can't inject script into the operator's UI.
    from homing_trade.web import DASHBOARD_HTML
    assert "const esc=" in DASHBOARD_HTML
    assert "${esc(p.rationale)}" in DASHBOARD_HTML        # proposal text escaped
    assert "${esc(b.rationale)}" in DASHBOARD_HTML        # brain-log text escaped
    # regression guard: no model-authored field interpolated WITHOUT esc()
    for bad in ("${p.rationale}", "${b.rationale}", "${b.observation}", "${b.prediction}",
                "${x.ai.rationale}", "${p.strategy||''}"):
        assert bad not in DASHBOARD_HTML, f"unescaped model text: {bad}"


def test_http_proposal_rejects_bool_id(tmp_path):
    cfg = Config(db_path=str(tmp_path / "bid.db"), web_port=0)
    Database(cfg.db_path).close()
    ctrl = Controller(cfg, runner=_blocking_runner, notifier=_N())
    server = make_server(cfg, ctrl)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True); t.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/proposal",
            data=json.dumps({"id": True, "decision": "approve"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "expected 400"
        except urllib.error.HTTPError as e:
            assert e.code == 400                          # bool id must not resolve to id 1
    finally:
        server.shutdown()


def test_http_proposal_endpoint_approve_applies(tmp_path):
    cfg = Config(db_path=str(tmp_path / "hp.db"), web_port=0)
    db = Database(cfg.db_path); db.ensure_strategy("ma", 5000.0)
    pid = db.create_proposal("ma", "playbook", {"version": "ma-v1", "rules": ["trend only"]},
                             "why", 1000)
    db.close()
    ctrl = Controller(cfg, runner=_blocking_runner, notifier=_N())
    server = make_server(cfg, ctrl)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True); t.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/proposal",
            data=json.dumps({"id": pid, "decision": "approve"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        resp = json.loads(urllib.request.urlopen(req, timeout=5).read())
        assert resp["ok"] and resp["status"] == "applied"
        # bad input -> 400
        bad = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/proposal",
            data=json.dumps({"decision": "approve"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(bad, timeout=5)
            assert False, "expected 400"
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        server.shutdown()
    repo = Repository.open(cfg.db_path)
    assert repo.latest_playbook("ma")["version"] == "ma-v1"
    repo.close()


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
    # proposal-queue UI is wired (Phase-3 #4): the panel + the approve/reject action
    assert "/api/proposal" in DASHBOARD_HTML and "decideProposal" in DASHBOARD_HTML
    assert "proposal queue" in DASHBOARD_HTML


def test_dashboard_renders_per_ai_cost_column():
    # Phase 5 #5: the per-AI cost column (tokens + $) + a usd formatter, fed by build_state's
    # x.cost, and the enable/disable toggle (from Phase 3) are both wired into the leaderboard card.
    from homing_trade.web import DASHBOARD_HTML
    assert "const usd=" in DASHBOARD_HTML                 # money formatter
    assert "x.cost" in DASHBOARD_HTML                     # reads the per-AI cost rollup
    assert "AI cost" in DASHBOARD_HTML and "tok" in DASHBOARD_HTML
    assert "toggleStrategy(" in DASHBOARD_HTML            # enable/disable toggle present


def test_dashboard_renders_signal_freshness_panel():
    # Phase 6 #6: the research-signals cache-freshness panel is wired.
    from homing_trade.web import DASHBOARD_HTML
    assert "research signals" in DASHBOARD_HTML and 'id="signals"' in DASHBOARD_HTML
    assert "s.signals" in DASHBOARD_HTML and "ageStr" in DASHBOARD_HTML
