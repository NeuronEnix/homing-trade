# tests/test_backtest.py
from algotrading.backtest import run_backtest
from algotrading.config import CONFIG
from algotrading.skills.ma_trend import MaTrend
from algotrading.models import Candle


def candles_from(prices):
    return [Candle(open=p, high=p + 1, low=p - 1, close=p, volume=1.0, time=1000 + i * 60000)
            for i, p in enumerate(prices)]


def test_run_backtest_returns_expected_keys():
    candles = candles_from([float(x) for x in range(1, 130)])
    r = run_backtest(MaTrend(), candles, CONFIG, 5000.0)
    for k in ("strategy", "trades", "final_equity", "return_pct", "win_rate",
              "profit_factor", "max_drawdown", "sharpe", "avg_win", "avg_loss",
              "equity_curve"):
        assert k in r
    assert r["strategy"] == "ma_trend"
    assert len(r["equity_curve"]) > 0


def test_run_backtest_deterministic_values():
    prices = [float(x) for x in list(range(50, 10, -1)) + list(range(10, 50)) + list(range(50, 10, -1))]
    candles = candles_from(prices)
    r1 = run_backtest(MaTrend(), candles, CONFIG, 5000.0)
    r2 = run_backtest(MaTrend(), candles, CONFIG, 5000.0)
    assert r1["return_pct"] == r2["return_pct"]
    assert r1["trades"] == r2["trades"]
    # equity VALUES are deterministic (timestamps may differ run-to-run)
    assert [e for _, e in r1["equity_curve"]] == [e for _, e in r2["equity_curve"]]


def test_run_backtest_flat_series_no_trades():
    candles = candles_from([100.0] * 130)
    r = run_backtest(MaTrend(), candles, CONFIG, 5000.0)
    assert r["trades"] == 0
