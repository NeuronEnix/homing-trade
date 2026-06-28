# tests/test_engine_regime_align.py
# The hard regime-alignment gate: when regime_align_enabled, a mechanical strategy may only OPEN a
# position whose direction fits the current regime. Flat candles classify as 'chop', so a TREND-style
# strategy (name 'macd') must be BLOCKED from going long, while a REVERT-style ('rsi_revert') may.
from homing_trade.engine import process_tick
from homing_trade.db import Database
from homing_trade.broker import Broker
from homing_trade.config import Config
from homing_trade.models import Candle, Signal
from homing_trade.skills.base import Strategy


class Scripted(Strategy):
    def __init__(self, name, actions):
        self.name = name
        self.actions = list(actions)

    def on_candle(self, candles, position):
        action = self.actions.pop(0) if self.actions else "HOLD"
        return Signal(action=action, confidence=0.9, reason="scripted", indicators={})


def _candles():
    # Flat tape -> ADX ~ 0 -> regime 'chop'; the 3.5% stop never trips between ticks.
    return [Candle(open=100, high=100.5, low=99.5, close=100, volume=1, time=1000 + i * 60000)
            for i in range(70)]


def _run(tmp_path, name, action, align):
    cfg = Config(db_path=str(tmp_path / "align.db"), enabled_skills=[name],
                 regime_align_enabled=align)
    db = Database(cfg.db_path)
    broker = Broker(cfg.fee, cfg.slippage)
    db.ensure_strategy(name, cfg.starting_balance)
    skill = Scripted(name, [action])
    process_tick(db, broker, [skill], _candles(), cfg)
    pos = db.get_open_position(name)
    last = db.conn.execute("SELECT taken_action, rejection_rationale FROM decision_log "
                           "WHERE strategy=? ORDER BY id DESC LIMIT 1", (name,)).fetchone()
    db.close()
    return pos, last


def test_trend_long_blocked_in_chop_when_align_on(tmp_path):
    pos, last = _run(tmp_path, "macd", "LONG", align=True)
    assert pos is None                                  # not opened
    assert last["taken_action"] == "BLOCKED"
    assert "not aligned" in (last["rejection_rationale"] or "")


def test_trend_long_opens_in_chop_when_align_off(tmp_path):
    # Control: same setup, gate off -> the entry is taken. Proves the gate is what blocks above.
    pos, last = _run(tmp_path, "macd", "LONG", align=False)
    assert pos is not None and pos.side == "LONG"
    assert last["taken_action"] == "LONG"


def test_reverter_long_allowed_in_chop_when_align_on(tmp_path):
    pos, last = _run(tmp_path, "rsi_revert", "LONG", align=True)
    assert pos is not None and pos.side == "LONG"       # reverters trade chop
    assert last["taken_action"] == "LONG"
