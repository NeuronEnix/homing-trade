"""Phase 7 #7: the continuous walk-forward backtest job.

Covers the v13 backtest_results ledger (record + recent + latest-per-strategy dedup, audit-truth), the
BacktestRunner cadence gate / disabled no-op / isolation (one bad strategy doesn't block the rest) /
no-candles guard / default strategy set, and that each run records the OOS + trusted aggregates. All
offline + deterministic (injected candle provider + clock)."""
from homing_trade.db import Database, AUDIT_TRUTH_TABLES, SCHEMA_VERSION
from homing_trade.repository import Repository
from homing_trade.backtest_job import BacktestRunner, CANDIDATE_STRATEGIES
from homing_trade.config import Config
from homing_trade.models import Candle
from homing_trade.profit_mirage import cutoff_ms_from_iso


def candles(n, t0=0, dt=60000):
    # a gentle zig-zag so strategies occasionally trade; values don't matter for recording
    return [Candle(open=100 + (i % 7), high=102 + (i % 7), low=98 + (i % 7),
                   close=100 + (i % 7), volume=1.0, time=t0 + i * dt) for i in range(n)]


def _cfg(**kw):
    base = dict(continuous_backtest_enabled=True, continuous_backtest_train=20,
                continuous_backtest_test=20, continuous_backtest_window=15, trust_cutoff_iso="")
    base.update(kw)
    return Config(**base)


# --- ledger (v13) ---
def test_schema_v13_and_audit_truth():
    assert SCHEMA_VERSION >= 13 and "backtest_results" in AUDIT_TRUTH_TABLES


def test_record_recent_and_latest_dedup(tmp_path):
    db = Database(str(tmp_path / "b.db"))
    oos = {"folds": 5, "compounded_return_pct": 1.2, "mean_sharpe": 0.3, "hit_rate": 0.6,
           "worst_drawdown": 0.1, "total_trades": 9}
    trusted = {"folds": 2, "compounded_return_pct": -0.4, "mean_sharpe": -0.1, "hit_rate": 0.5}
    db.record_backtest_result(1000, "ma_trend", pair="P", interval="15m", train=20, test=20,
                              window=15, cutoff_ms=None, oos=oos, trusted=trusted)
    db.record_backtest_result(2000, "ma_trend", pair="P", interval="15m", train=20, test=20,
                              window=15, cutoff_ms=None, oos={**oos, "compounded_return_pct": 9.9},
                              trusted=trusted)
    assert len(db.recent_backtest_results()) == 2
    assert len(db.recent_backtest_results(strategy="ma_trend")) == 2
    latest = db.latest_backtest_per_strategy()
    assert len(latest) == 1 and latest[0]["oos_return_pct"] == 9.9   # newest row wins
    db.close()


# --- runner ---
def test_runner_runs_and_records(tmp_path):
    repo = Repository.open(str(tmp_path / "b.db"))
    run = BacktestRunner(repo, _cfg(), candle_provider=lambda: candles(120),
                         clock=lambda: 1000.0, strategies=["ma_trend", "rsi_revert"])
    out = run.run()
    assert {r["strategy"] for r in out} == {"ma_trend", "rsi_revert"}
    latest = repo.latest_backtest_per_strategy()
    assert {r["strategy"] for r in latest} == {"ma_trend", "rsi_revert"}
    row = repo.recent_backtest_results(strategy="ma_trend")[0]
    assert row["folds"] == 5 and row["trusted_folds"] == row["folds"]   # no cutoff -> all trusted
    repo.close()


def test_runner_cadence_gated(tmp_path):
    repo = Repository.open(str(tmp_path / "b.db"))
    clock = [1000.0]
    run = BacktestRunner(repo, _cfg(), candle_provider=lambda: candles(120),
                         clock=lambda: clock[0], strategies=["ma_trend"], poll_sec=3600)
    assert len(run.run()) == 1
    clock[0] += 100
    assert run.run() == []                                  # within cadence -> skipped
    clock[0] += 3600
    assert len(run.run()) == 1                              # cadence due -> runs again
    assert len(repo.recent_backtest_results()) == 2
    repo.close()


def test_runner_disabled_is_noop(tmp_path):
    repo = Repository.open(str(tmp_path / "b.db"))
    run = BacktestRunner(repo, Config(continuous_backtest_enabled=False),
                         candle_provider=lambda: candles(120), strategies=["ma_trend"])
    assert run.enabled is False and run.run() == []
    assert repo.recent_backtest_results() == []
    repo.close()


def test_runner_skips_unknown_strategy(tmp_path):
    repo = Repository.open(str(tmp_path / "b.db"))
    run = BacktestRunner(repo, _cfg(), candle_provider=lambda: candles(120), clock=lambda: 1.0,
                         strategies=["ma_trend", "does_not_exist"])
    out = run.run()
    assert [r["strategy"] for r in out] == ["ma_trend"]     # unknown skipped, real one still ran
    repo.close()


def test_runner_no_candles_records_nothing(tmp_path):
    repo = Repository.open(str(tmp_path / "b.db"))
    run = BacktestRunner(repo, _cfg(), candle_provider=lambda: [], clock=lambda: 1.0,
                         strategies=["ma_trend"])
    assert run.run() == [] and repo.recent_backtest_results() == []
    repo.close()


def test_runner_records_trusted_subset_under_cutoff(tmp_path):
    # candles span across a mid-history cutoff -> trusted (post-cutoff) folds are strictly fewer
    repo = Repository.open(str(tmp_path / "b.db"))
    t0 = cutoff_ms_from_iso("2025-11-01")
    cs = candles(120, t0=t0, dt=86_400_000)             # one candle/day from 2025-11-01
    run = BacktestRunner(repo, _cfg(trust_cutoff_iso="2026-01-01"),
                         candle_provider=lambda: cs, clock=lambda: 1.0, strategies=["ma_trend"])
    run.run()
    row = repo.recent_backtest_results(strategy="ma_trend")[0]
    assert 0 < row["trusted_folds"] < row["folds"]      # some, but not all, folds are post-cutoff
    assert row["cutoff_ms"] == cutoff_ms_from_iso("2026-01-01")
    repo.close()


def test_runner_records_zero_folds_on_thin_history(tmp_path):
    # fewer candles than train+test -> no folds -> a row is still recorded with folds=0 (not skipped)
    repo = Repository.open(str(tmp_path / "b.db"))
    run = BacktestRunner(repo, _cfg(), candle_provider=lambda: candles(30), clock=lambda: 1.0,
                         strategies=["ma_trend"])
    out = run.run()
    assert len(out) == 1
    row = repo.recent_backtest_results(strategy="ma_trend")[0]
    assert row["folds"] == 0 and row["oos_trades"] == 0
    repo.close()


def test_transient_empty_read_does_not_burn_cadence(tmp_path):
    # an empty candle read must NOT stamp the cadence: the next tick (with candles) still runs
    repo = Repository.open(str(tmp_path / "b.db"))
    state = {"cs": []}
    run = BacktestRunner(repo, _cfg(), candle_provider=lambda: state["cs"], clock=lambda: 1000.0,
                         strategies=["ma_trend"], poll_sec=3600)
    assert run.run() == []                               # empty read -> no-op, cadence NOT consumed
    state["cs"] = candles(120)
    assert len(run.run()) == 1                           # same clock, still runs (cadence intact)
    repo.close()


def test_default_strategies_include_candidates(tmp_path):
    repo = Repository.open(str(tmp_path / "b.db"))
    run = BacktestRunner(repo, _cfg())
    for c in CANDIDATE_STRATEGIES:
        assert c in run.strategies
    assert "ma_trend" in run.strategies                     # an enabled skill too
    repo.close()
