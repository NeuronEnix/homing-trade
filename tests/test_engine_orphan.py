# tests/test_engine_orphan.py
# Safety: a position whose strategy is no longer in the roster (demoted out of enabled_skills) must
# still be risk-managed — its stop/liquidation enforced — instead of riding unmanaged forever.
from homing_trade.engine import process_tick
from homing_trade.db import Database
from homing_trade.broker import Broker
from homing_trade.config import Config
from homing_trade.models import Candle, Position, Signal
from homing_trade.skills.base import Strategy


class Idle(Strategy):
    name = "ma_trend"   # the surviving roster member; never trades in this test

    def on_candle(self, candles, position):
        return Signal(action="HOLD", reason="idle")


def _candles(last_low):
    # flat at 100 except the final candle dips so a LONG stop at 98 is breached
    cs = [Candle(open=100, high=100.5, low=99.5, close=100, volume=1, time=1000 + i * 60000)
          for i in range(60)]
    cs[-1] = Candle(open=100, high=100.5, low=last_low, close=last_low, volume=1, time=1000 + 60 * 60000)
    return cs


def test_demoted_strategy_position_still_gets_stopped(tmp_path):
    cfg = Config(db_path=str(tmp_path / "orphan.db"), enabled_skills=["ma_trend"])
    db = Database(cfg.db_path)
    broker = Broker(cfg.fee, cfg.slippage)
    db.ensure_strategy("ma_trend", cfg.starting_balance)
    db.ensure_strategy("grid", cfg.starting_balance)          # demoted strategy, not in roster
    # an open grid LONG with a stop at 98 — grid is NOT in the roster passed to process_tick
    db.open_position(Position(strategy="grid", side="LONG", entry_price=100, size=0.1,
                              leverage=10, margin=1, stop_price=98, opened_at=0))
    assert db.get_open_position("grid") is not None

    process_tick(db, broker, [Idle()], _candles(last_low=90), cfg)   # roster = [ma_trend] only

    pos = db.get_open_position("grid")
    assert pos is None                                        # orphan stop was enforced
    closed = db.conn.execute(
        "SELECT exit_reason FROM trades WHERE strategy='grid' AND action='CLOSE'").fetchall()
    assert any(r["exit_reason"] in ("stop", "liquidation") for r in closed)
    db.close()


def test_demoted_strategy_position_held_when_stop_not_hit(tmp_path):
    # control: no breach -> the orphan position survives (we only enforce risk, not force-close)
    cfg = Config(db_path=str(tmp_path / "orphan2.db"), enabled_skills=["ma_trend"])
    db = Database(cfg.db_path)
    broker = Broker(cfg.fee, cfg.slippage)
    db.ensure_strategy("ma_trend", cfg.starting_balance)
    db.ensure_strategy("grid", cfg.starting_balance)
    db.open_position(Position(strategy="grid", side="LONG", entry_price=100, size=0.1,
                              leverage=10, margin=1, stop_price=98, opened_at=0))

    process_tick(db, broker, [Idle()], _candles(last_low=99.5), cfg)  # no breach

    assert db.get_open_position("grid") is not None           # still open, just risk-checked
    db.close()
