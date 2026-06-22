from homing_trade.repository import Repository
from homing_trade.report import compute_stats, leaderboard, format_leaderboard


def seed(tmp_path):
    repo = Repository.open(str(tmp_path / "rep.db"))
    repo.ensure_strategy("ma_trend", 5000.0)
    # two closed trades: one win (+200), one loss (-100)
    repo.record_trade("ma_trend", 1, "LONG", "CLOSE", 110.0, 2.0, 0.1, 200.0, 1000)
    repo.record_trade("ma_trend", 2, "LONG", "CLOSE", 90.0, 2.0, 0.1, -100.0, 2000)
    repo.record_equity("ma_trend", 5100.0, 2000)
    return repo


def test_compute_stats_counts_wins_losses(tmp_path):
    repo = seed(tmp_path)
    stats = compute_stats(repo, "ma_trend", 5000.0)
    assert stats["trades"] == 2
    assert stats["wins"] == 1
    assert stats["losses"] == 1
    assert stats["win_rate"] == 0.5
    assert stats["equity"] == 5100.0
    assert round(stats["return_pct"], 2) == 2.0


def test_leaderboard_sorted_by_equity(tmp_path):
    repo = seed(tmp_path)
    repo.ensure_strategy("grid", 5000.0)
    repo.record_equity("grid", 5200.0, 2000)
    rows = leaderboard(repo, ["ma_trend", "grid"], 5000.0)
    assert rows[0]["strategy"] == "grid"  # higher equity first


def test_format_leaderboard_is_string(tmp_path):
    repo = seed(tmp_path)
    rows = leaderboard(repo, ["ma_trend"], 5000.0)
    out = format_leaderboard(rows)
    assert "ma_trend" in out and "equity" in out.lower()
