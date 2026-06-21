from homing_trade.db import Database
from homing_trade.models import Position


def make_db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def test_ensure_strategy_idempotent(tmp_path):
    db = make_db(tmp_path)
    db.ensure_strategy("ma_trend", 5000.0)
    db.ensure_strategy("ma_trend", 5000.0)  # second call must not duplicate/reset
    assert db.get_balance("ma_trend") == 5000.0


def test_set_and_get_balance(tmp_path):
    db = make_db(tmp_path)
    db.ensure_strategy("rsi_revert", 5000.0)
    db.set_balance("rsi_revert", 4800.0)
    assert db.get_balance("rsi_revert") == 4800.0


def test_open_get_close_position(tmp_path):
    db = make_db(tmp_path)
    db.ensure_strategy("ma_trend", 5000.0)
    pos = Position(strategy="ma_trend", side="LONG", entry_price=100.0, size=0.5,
                   leverage=3.0, margin=50.0, stop_price=98.0, opened_at=1000)
    pid = db.open_position(pos)
    assert isinstance(pid, int)
    fetched = db.get_open_position("ma_trend")
    assert fetched is not None and fetched.id == pid and fetched.side == "LONG"
    db.close_position(pid)
    assert db.get_open_position("ma_trend") is None


def test_state_roundtrip(tmp_path):
    db = make_db(tmp_path)
    assert db.get_state("last_candle_time") is None
    db.set_state("last_candle_time", "1717000000000")
    assert db.get_state("last_candle_time") == "1717000000000"


def test_records_do_not_raise(tmp_path):
    db = make_db(tmp_path)
    db.ensure_strategy("grid", 5000.0)
    db.record_trade("grid", None, "LONG", "OPEN", 100.0, 0.5, 0.05, 0.0, 1000)
    db.record_equity("grid", 5000.0, 1000)
    db.log_decision("grid", 1000, 999, "HOLD", 0.0, "no signal", {"rsi": 55})
