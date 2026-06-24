# tests/test_engine_reversal.py
# A directional signal opposite to an open position is a REVERSAL: the strategy's own thesis flipped.
# The engine must always CLOSE the position (else a trend strategy with no CLOSE signal rides it to
# the stop) and, when reversal_flip_enabled, OPEN the opposite side so it can trade the new direction.
from homing_trade.engine import process_tick
from homing_trade.db import Database
from homing_trade.broker import Broker
from homing_trade.config import Config
from homing_trade.models import Candle, Signal
from homing_trade.skills.base import Strategy


class Scripted(Strategy):
    """Emits a queued sequence of actions, one per tick, ignoring the candles."""
    name = "scripted"

    def __init__(self, actions):
        self.actions = list(actions)

    def on_candle(self, candles, position):
        action = self.actions.pop(0) if self.actions else "HOLD"
        return Signal(action=action, confidence=0.9, reason="scripted", indicators={})


def _candles():
    # Flat price: a 2% stop sits at 98 (long) / 102 (short), and the range stays 99.5-100.5, so a
    # position is never stopped out between ticks — isolating the reversal logic under test.
    return [Candle(open=100, high=100.5, low=99.5, close=100, volume=1, time=1000 + i * 60000)
            for i in range(70)]


def _setup(tmp_path, actions, **cfg_kw):
    cfg = Config(db_path=str(tmp_path / "rev.db"), enabled_skills=["scripted"], **cfg_kw)
    db = Database(cfg.db_path)
    broker = Broker(cfg.fee, cfg.slippage)
    db.ensure_strategy("scripted", cfg.starting_balance)
    skill = Scripted(actions)
    return cfg, db, broker, skill


def _run_ticks(db, broker, skill, cfg, n, **kw):
    for _ in range(n):
        process_tick(db, broker, [skill], _candles(), cfg, **kw)


def test_reversal_flips_when_enabled(tmp_path):
    cfg, db, broker, skill = _setup(tmp_path, ["LONG", "SHORT"], reversal_flip_enabled=True)
    _run_ticks(db, broker, skill, cfg, 1)
    assert db.get_open_position("scripted").side == "LONG"
    _run_ticks(db, broker, skill, cfg, 1)
    pos = db.get_open_position("scripted")
    assert pos is not None and pos.side == "SHORT"          # flipped, not just closed
    # the long was closed with a reversal exit_reason
    closed = db.conn.execute(
        "SELECT exit_reason FROM trade_outcomes WHERE strategy='scripted'").fetchall()
    assert any(r["exit_reason"] == "reversal" for r in closed)
    db.close()


def test_reversal_closes_only_when_flip_disabled(tmp_path):
    cfg, db, broker, skill = _setup(tmp_path, ["LONG", "SHORT"], reversal_flip_enabled=False)
    _run_ticks(db, broker, skill, cfg, 2)
    assert db.get_open_position("scripted") is None         # closed, NOT reopened short
    taken = [r["taken_action"] for r in db.conn.execute(
        "SELECT taken_action FROM decision_log WHERE strategy='scripted' ORDER BY id").fetchall()]
    assert taken == ["LONG", "CLOSE"]
    db.close()


def test_same_side_signal_is_noop(tmp_path):
    cfg, db, broker, skill = _setup(tmp_path, ["LONG", "LONG"], reversal_flip_enabled=True)
    _run_ticks(db, broker, skill, cfg, 2)
    pos = db.get_open_position("scripted")
    assert pos is not None and pos.side == "LONG"
    # exactly one OPEN trade — the second LONG did not open a second position
    opens = db.conn.execute(
        "SELECT COUNT(*) AS c FROM trades WHERE strategy='scripted' AND action='OPEN'").fetchone()["c"]
    assert opens == 1
    last = db.conn.execute(
        "SELECT taken_action, rejection_rationale FROM decision_log "
        "WHERE strategy='scripted' ORDER BY id DESC LIMIT 1").fetchone()
    assert last["taken_action"] == "HOLD"
    assert "same side" in (last["rejection_rationale"] or "")
    db.close()


def test_paused_reversal_closes_but_does_not_reopen(tmp_path):
    # Open a long while not paused, then deliver the reversal while paused: must close, never flip.
    cfg, db, broker, skill = _setup(tmp_path, ["LONG", "SHORT"], reversal_flip_enabled=True)
    _run_ticks(db, broker, skill, cfg, 1)                    # not paused -> opens LONG
    assert db.get_open_position("scripted").side == "LONG"
    _run_ticks(db, broker, skill, cfg, 1, is_paused=lambda: True)
    assert db.get_open_position("scripted") is None          # closed; flip suppressed while paused
    db.close()
