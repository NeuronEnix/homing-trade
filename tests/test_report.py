from algotrading.db import Database
from algotrading.report import compute_stats, leaderboard, format_leaderboard


def seed(tmp_path):
    db = Database(str(tmp_path / "rep.db"))
    db.ensure_strategy("ma_trend", 5000.0)
    # two closed trades: one win (+200), one loss (-100)
    db.record_trade("ma_trend", 1, "LONG", "CLOSE", 110.0, 2.0, 0.1, 200.0, 1000)
    db.record_trade("ma_trend", 2, "LONG", "CLOSE", 90.0, 2.0, 0.1, -100.0, 2000)
    db.record_equity("ma_trend", 5100.0, 2000)
    return db


def test_compute_stats_counts_wins_losses(tmp_path):
    db = seed(tmp_path)
    stats = compute_stats(db, "ma_trend", 5000.0)
    assert stats["trades"] == 2
    assert stats["wins"] == 1
    assert stats["losses"] == 1
    assert stats["win_rate"] == 0.5
    assert stats["equity"] == 5100.0
    assert round(stats["return_pct"], 2) == 2.0


def test_leaderboard_sorted_by_equity(tmp_path):
    db = seed(tmp_path)
    db.ensure_strategy("grid", 5000.0)
    db.record_equity("grid", 5200.0, 2000)
    rows = leaderboard(db, ["ma_trend", "grid"], 5000.0)
    assert rows[0]["strategy"] == "grid"  # higher equity first


def test_format_leaderboard_is_string(tmp_path):
    db = seed(tmp_path)
    rows = leaderboard(db, ["ma_trend"], 5000.0)
    out = format_leaderboard(rows)
    assert "ma_trend" in out and "equity" in out.lower()
