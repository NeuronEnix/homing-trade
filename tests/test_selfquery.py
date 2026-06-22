from homing_trade.selfquery import SelfQuery
from homing_trade.repository import Repository


def seed(tmp_path):
    repo = Repository.open(str(tmp_path / "sq.db"))
    repo.ensure_strategy("ma_trend", 5000.0)
    # two wins (+100, +60), one loss (-40)
    repo.record_trade("ma_trend", 1, "LONG", "CLOSE", 110, 1, 0.1, 100.0, 1000)
    repo.record_trade("ma_trend", 2, "LONG", "CLOSE", 90, 1, 0.1, -40.0, 2000)
    repo.record_trade("ma_trend", 3, "LONG", "CLOSE", 120, 1, 0.1, 60.0, 3000)
    repo.record_equity("ma_trend", 5060.0, 1000)
    repo.record_equity("ma_trend", 5020.0, 2000)   # a dip -> drawdown
    repo.record_equity("ma_trend", 5080.0, 3000)
    repo.log_decision("ma_trend", 1000, 999, "LONG", 0.8, "x", {}, taken_action="LONG")
    repo.log_decision("ma_trend", 2000, 1999, "HOLD", 0.0, "x", {}, taken_action="HOLD")
    repo.log_decision("ma_trend", 3000, 2999, "LONG", 0.7, "x", {}, taken_action="BLOCKED",
                      rejection_rationale="cap")
    repo.record_risk_event(3000, "ma_trend", "veto", "cap", 999.0)
    return repo


def test_performance_metrics(tmp_path):
    sq = SelfQuery(seed(tmp_path), starting_balance=5000.0)
    p = sq.performance("ma_trend")
    assert p["trades"] == 3
    assert p["realized_pnl"] == 120.0
    assert round(p["win_rate"], 4) == round(2 / 3, 4)
    assert p["profit_factor"] == 160.0 / 40.0     # gross profit 160, gross loss 40
    assert round(p["expectancy"], 2) == 40.0      # 120 / 3
    assert p["equity"] == 5080.0                  # last snapshot
    assert p["max_drawdown"] > 0                  # 5060 -> 5020 dip
    assert "sharpe" in p


def test_performance_empty_strategy(tmp_path):
    repo = Repository.open(str(tmp_path / "e.db"))
    repo.ensure_strategy("grid", 5000.0)
    p = SelfQuery(repo, 5000.0).performance("grid")
    assert p["trades"] == 0 and p["win_rate"] == 0.0 and p["equity"] == 5000.0  # falls back to balance


def test_leaderboard_sorted(tmp_path):
    repo = seed(tmp_path)
    repo.ensure_strategy("grid", 5000.0)
    repo.record_equity("grid", 6000.0, 1000)
    rows = SelfQuery(repo, 5000.0).leaderboard(["ma_trend", "grid"])
    assert rows[0]["strategy"] == "grid"          # higher equity first


def test_risk_and_decision_breakdown(tmp_path):
    sq = SelfQuery(seed(tmp_path), 5000.0)
    assert sq.risk_event_counts().get("veto") == 1
    bd = sq.decision_breakdown("ma_trend")
    assert bd.get("LONG") == 1 and bd.get("HOLD") == 1 and bd.get("BLOCKED") == 1


def test_profit_factor_none_when_no_losses(tmp_path):
    repo = Repository.open(str(tmp_path / "w.db"))
    repo.ensure_strategy("ma_trend", 5000.0)
    repo.record_trade("ma_trend", 1, "LONG", "CLOSE", 110, 1, 0.1, 50.0, 1000)  # only a win
    p = SelfQuery(repo, 5000.0).performance("ma_trend")
    assert p["profit_factor"] is None     # JSON-safe: undefined when there are no losses
    assert p["win_rate"] == 1.0


class _RecordingRepo:
    """Wraps a repo and records every method name SelfQuery touches."""
    READS = {"closed_pnls", "equity_series", "get_balance",
             "recent_risk_events", "taken_action_counts"}

    def __init__(self, real):
        self._real = real
        self.calls = []

    def __getattr__(self, name):
        self.calls.append(name)
        return getattr(self._real, name)


def test_selfquery_only_calls_read_methods(tmp_path):
    spy = _RecordingRepo(seed(tmp_path))
    sq = SelfQuery(spy, 5000.0)
    sq.performance("ma_trend")
    sq.leaderboard(["ma_trend"])
    sq.risk_event_counts()
    sq.decision_breakdown("ma_trend")
    assert spy.calls                                  # it did call the repo
    assert set(spy.calls) <= _RecordingRepo.READS     # ...and ONLY read methods (no writes)
