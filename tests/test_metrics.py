# tests/test_metrics.py
import math
from algotrading import metrics


def closes(*pnls):
    return [{"action": "CLOSE", "pnl": p} for p in pnls]


def test_periods_per_year_1m():
    assert metrics.periods_per_year("1m") == 525600.0


def test_total_return_pct():
    assert metrics.total_return_pct(5000.0, 5100.0) == 2.0
    assert metrics.total_return_pct(0.0, 100.0) == 0.0


def test_win_rate():
    assert metrics.win_rate(closes(10, -5, 20)) == 2 / 3
    assert metrics.win_rate([]) == 0.0


def test_profit_factor():
    assert metrics.profit_factor(closes(10, 20, -5)) == 30 / 5
    assert metrics.profit_factor(closes(10, 20)) == float("inf")
    assert metrics.profit_factor([]) == 0.0


def test_avg_win_loss():
    assert metrics.avg_win(closes(10, 20, -6)) == 15.0
    assert metrics.avg_loss(closes(10, -4, -6)) == -5.0


def test_max_drawdown():
    curve = [(0, 100.0), (1, 120.0), (2, 90.0), (3, 150.0)]
    assert metrics.max_drawdown(curve) == (120.0 - 90.0) / 120.0  # 0.25


def test_sharpe_zero_variance_returns_zero():
    # constant 10% step -> identical returns -> zero variance -> 0.0
    assert metrics.sharpe([(0, 100.0), (1, 110.0), (2, 121.0)], 1.0) == 0.0


def test_sharpe_positive_for_varied_growth():
    assert metrics.sharpe([(0, 100.0), (1, 110.0), (2, 120.0)], 1.0) > 0


def test_sharpe_too_few_points():
    assert metrics.sharpe([(0, 100.0)], 525600.0) == 0.0
