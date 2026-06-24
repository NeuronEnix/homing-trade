# tests/test_exit_command.py
# A UI "exit trade" click must close the position promptly AND survive a feed outage — it goes
# through a command queue drained by the engine thread. These cover the two fixes:
#   1) Controller wakes the poll loop immediately on a queued command (no full-poll delay).
#   2) run() drains commands even on a tick where the live feed returned no candle.
import queue

from homing_trade.engine import run, SkillRunner
from homing_trade.db import Database
from homing_trade.broker import Broker
from homing_trade.config import Config
from homing_trade.models import Candle
from homing_trade.position_manager import PositionManager
from homing_trade.skills.ma_trend import MaTrend
from homing_trade.web import Controller


def _flat_candles(n=70):
    return [Candle(open=100, high=100.5, low=99.5, close=100, volume=1, time=1000 + i * 60000)
            for i in range(n)]


# ---- Controller wake/queue plumbing ----

def test_close_trade_enqueues_and_wakes(tmp_path):
    ctrl = Controller(Config(db_path=str(tmp_path / "c.db")), runner=lambda *a, **k: None)
    assert not ctrl._wake.is_set()
    ctrl.close_trade("ma_trend")
    assert ctrl._commands.get_nowait() == {"action": "close", "strategy": "ma_trend"}
    assert ctrl._wake.is_set()                      # loop will wake immediately, not a poll later


def test_close_trade_ignores_empty_strategy(tmp_path):
    ctrl = Controller(Config(db_path=str(tmp_path / "c.db")), runner=lambda *a, **k: None)
    ctrl.close_trade(None)
    assert ctrl._commands.empty()
    assert not ctrl._wake.is_set()


def test_stop_wakes_the_loop(tmp_path):
    ctrl = Controller(Config(db_path=str(tmp_path / "c.db")), runner=lambda *a, **k: None)
    ctrl.stop()
    assert ctrl._wake.is_set()


def test_interruptible_sleep_returns_immediately_when_woken(tmp_path):
    ctrl = Controller(Config(db_path=str(tmp_path / "c.db")), runner=lambda *a, **k: None)
    ctrl._wake.set()
    ctrl._interruptible_sleep(30)                   # must NOT block for 30s — event is already set
    assert not ctrl._wake.is_set()                  # and it clears the flag


# ---- engine: drain during a feed outage ----

def _open_long(db, broker, cfg, strategy="ma_trend"):
    db.ensure_strategy(strategy, cfg.starting_balance)
    pm = PositionManager(db, broker, cfg)
    opened, _ = pm.open(MaTrend(), "LONG", _flat_candles()[-1], 1000)
    assert opened and db.get_open_position(strategy) is not None


def test_drain_commands_method_closes_position(tmp_path):
    cfg = Config(db_path=str(tmp_path / "d.db"), enabled_skills=["ma_trend"])
    db, broker = Database(cfg.db_path), Broker(cfg.fee, cfg.slippage)
    _open_long(db, broker, cfg)
    runner = SkillRunner(cfg, db, broker)
    q = queue.Queue(); q.put({"action": "close", "strategy": "ma_trend"})
    runner.drain_commands(q, _flat_candles()[-1])
    assert db.get_open_position("ma_trend") is None
    db.close()


def test_run_closes_during_feed_outage(tmp_path):
    # tick1: feed up (position stays open) -> last_candle cached. tick2: feed DOWN and the operator
    # clicks exit -> the queued close must still execute against the last known price.
    cfg = Config(db_path=str(tmp_path / "o.db"), enabled_skills=["ma_trend"])
    db, broker = Database(cfg.db_path), Broker(cfg.fee, cfg.slippage)
    _open_long(db, broker, cfg)
    db.close()

    q = queue.Queue()
    raw = [{"open": c.open, "high": c.high, "low": c.low, "close": c.close,
            "volume": c.volume, "time": c.time} for c in _flat_candles()]
    calls = {"n": 0}

    def fetcher(url, params):
        calls["n"] += 1
        if calls["n"] == 1:
            return raw                      # feed up
        if calls["n"] == 2:
            q.put({"action": "close", "strategy": "ma_trend"})   # operator clicks exit during outage
        return []                            # feed down from tick 2 on

    run(cfg, fetcher=fetcher, max_ticks=3, sleeper=lambda s: None, commands=q)

    db = Database(cfg.db_path)
    assert db.get_open_position("ma_trend") is None        # closed despite the dead feed
    reason = db.conn.execute(
        "SELECT exit_reason FROM trades WHERE strategy='ma_trend' AND action='CLOSE' "
        "ORDER BY ts DESC LIMIT 1").fetchone()["exit_reason"]
    assert reason == "manual"
    db.close()
