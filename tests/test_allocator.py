# tests/test_allocator.py
from algotrading.allocator import compute_allocations, recent_performance
from algotrading.db import Database
from algotrading.ledger import MemoryLedger


def test_compute_allocations_winner_gets_more_all_above_floor():
    w = compute_allocations({"a": 5.0, "b": 1.0, "c": -2.0}, floor=0.1, cap=1.0)
    assert w["a"] > w["b"] > w["c"]
    assert all(v >= 0.1 - 1e-9 for v in w.values())
    assert all(v <= 1.0 + 1e-9 for v in w.values())


def test_compute_allocations_equal_scores_equal_weights():
    w = compute_allocations({"a": 1.0, "b": 1.0}, floor=0.2, cap=1.0)
    assert abs(w["a"] - w["b"]) < 1e-9


def test_compute_allocations_empty():
    assert compute_allocations({}) == {}


def test_recent_performance_db(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    db.ensure_strategy("ma_trend", 5000.0)
    db.record_trade("ma_trend", 1, "LONG", "CLOSE", 110, 1, 0.1, 20.0, 1000)
    db.record_trade("ma_trend", 2, "LONG", "CLOSE", 90, 1, 0.1, -10.0, 2000)
    assert recent_performance(db, "ma_trend", 20) == 5.0  # mean of [20, -10]
    db.close()


def test_recent_performance_ledger():
    led = MemoryLedger("rl_qlearn", 5000.0)
    led.record_trade("rl_qlearn", 1, "LONG", "CLOSE", 110, 1, 0.1, 4.0, 1000)
    led.record_trade("rl_qlearn", 1, "LONG", "OPEN", 100, 1, 0.1, -0.1, 900)  # ignored (not CLOSE)
    assert recent_performance(led, "rl_qlearn", 20) == 4.0
