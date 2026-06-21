from homing_trade.ledger import MemoryLedger
from homing_trade.models import Position


def test_balance_get_set():
    led = MemoryLedger("ma_trend", 5000.0)
    assert led.get_balance("ma_trend") == 5000.0
    led.set_balance("ma_trend", 4800.0)
    assert led.get_balance("ma_trend") == 4800.0


def test_open_get_close_position():
    led = MemoryLedger("ma_trend", 5000.0)
    pos = Position(strategy="ma_trend", side="LONG", entry_price=100.0, size=1.0,
                   leverage=3.0, margin=33.0, stop_price=98.0, opened_at=1000)
    pid = led.open_position(pos)
    assert pid == 1
    fetched = led.get_open_position("ma_trend")
    assert fetched is not None and fetched.id == 1 and fetched.side == "LONG"
    led.close_position(pid)
    assert led.get_open_position("ma_trend") is None


def test_records_accumulate():
    led = MemoryLedger("ma_trend", 5000.0)
    led.record_trade("ma_trend", 1, "LONG", "OPEN", 100.0, 1.0, 0.05, -0.05, 1000)
    led.record_trade("ma_trend", 1, "LONG", "CLOSE", 110.0, 1.0, 0.05, 9.9, 2000)
    led.record_equity("ma_trend", 5009.9, 2000)
    led.log_decision("ma_trend", 2000, 2000, "CLOSE", 0.6, "x", {"rsi": 71})
    assert len(led.trades) == 2
    assert led.trades[1]["action"] == "CLOSE"
    assert led.equity_curve == [(2000, 5009.9)]


def test_ensure_strategy_idempotent():
    led = MemoryLedger("ma_trend", 5000.0)
    led.ensure_strategy("ma_trend", 5000.0)  # must not reset
    led.set_balance("ma_trend", 100.0)
    led.ensure_strategy("ma_trend", 5000.0)
    assert led.get_balance("ma_trend") == 100.0
