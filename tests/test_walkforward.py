"""Phase 7 #1: the walk-forward evaluation harness.

Honest out-of-sample evaluation on top of backtest.run_backtest. Covers: fold tiling (contiguous,
non-overlapping OOS test windows + validation), that each test window is evaluated in isolation via
run_backtest, the fit→freeze→test gate (fit sees ONLY the train slice; its frozen cfg is applied to
the test eval), the compounded OOS aggregate + hit-rate, the no-lookahead invariant (mutating candles
AFTER a fold's test window cannot change that fold's result), and graceful empty-fold handling. All
offline + deterministic (synthetic candles; run_backtest stubbed where values must be exact)."""
from dataclasses import replace

import pytest

from homing_trade import walkforward as wf
from homing_trade.walkforward import walk_forward, fold_bounds, _format
from homing_trade.config import CONFIG
from homing_trade.skills.ma_trend import MaTrend
from homing_trade.skills.rsi_revert import RsiRevert
from homing_trade.models import Candle


def candles_from(prices):
    return [Candle(open=p, high=p + 1, low=p - 1, close=p, volume=1.0, time=1000 + i * 60000)
            for i, p in enumerate(prices)]


def _stub(name, *, return_pct=0.0, trades=1, sharpe=0.5, dd=0.1):
    return {"strategy": name, "trades": trades, "final_equity": 0.0, "return_pct": return_pct,
            "win_rate": 0.5, "profit_factor": 1.0, "max_drawdown": dd, "sharpe": sharpe,
            "avg_win": 0.0, "avg_loss": 0.0, "equity_curve": []}


# --- fold tiling ---
def test_fold_bounds_tile_test_windows_contiguously():
    fb = fold_bounds(n=100, train=30, test=10)            # step defaults to test
    assert fb[0] == (0, 30, 30, 40)
    assert fb[1] == (10, 40, 40, 50)
    assert len(fb) == 7                                    # lo in {0,10,...,60}
    tests = [(lo, hi) for _, _, lo, hi in fb]
    assert tests == [(t, t + 10) for t in range(30, 100, 10)]   # contiguous, no overlap, in-bounds


def test_fold_bounds_empty_when_too_few_candles():
    assert fold_bounds(n=20, train=30, test=10) == []


def test_fold_bounds_validates_positive():
    for bad in [(50, 0, 10, None), (50, 10, 0, None), (50, 10, 10, 0)]:
        with pytest.raises(ValueError):
            fold_bounds(bad[0], bad[1], bad[2], step=bad[3])


# --- evaluation drives run_backtest per test window ---
def test_evaluates_each_test_window_in_isolation(monkeypatch):
    seen = []
    def fake_rb(skill, cands, cfg, bal, window=200):
        seen.append((cands[0].time, cands[-1].time, len(cands)))
        return _stub(skill.name, return_pct=1.0)
    monkeypatch.setattr(wf, "run_backtest", fake_rb)
    cs = candles_from([float(i) for i in range(100)])
    out = walk_forward(MaTrend, cs, CONFIG, 5000.0, train=30, test=10)
    assert len(out["folds"]) == len(seen) == 7
    assert all(n == 10 for *_, n in seen)                  # each eval saw exactly its test slice
    # the eval slice is the test window, not the train window
    assert seen[0][0] == cs[30].time and seen[0][1] == cs[39].time


def test_oos_aggregate_compounds_returns(monkeypatch):
    rets = iter([10.0, -5.0, 20.0])
    monkeypatch.setattr(wf, "run_backtest",
                        lambda skill, cands, cfg, bal, window=200: _stub(skill.name,
                                                                         return_pct=next(rets),
                                                                         trades=2))
    cs = candles_from([float(i) for i in range(50)])       # train20 test10 step10 -> 3 folds
    out = walk_forward(MaTrend, cs, CONFIG, 5000.0, train=20, test=10)
    assert len(out["folds"]) == 3
    expected = 5000.0 * 1.10 * 0.95 * 1.20                 # compounded across folds
    assert out["oos"]["final_equity"] == pytest.approx(expected)
    assert out["oos"]["compounded_return_pct"] == pytest.approx((expected - 5000.0) / 5000.0 * 100)
    assert out["oos"]["hit_rate"] == pytest.approx(2 / 3)
    assert out["oos"]["total_trades"] == 6
    assert out["oos"]["worst_return_pct"] == -5.0


# --- fit -> freeze -> test gate ---
def test_fit_fn_sees_only_train_slice_and_freezes_cfg_for_test(monkeypatch):
    eval_cfgs, fit_calls = [], []
    def fake_rb(skill, cands, cfg, bal, window=200):
        eval_cfgs.append(cfg)
        return _stub(skill.name)
    monkeypatch.setattr(wf, "run_backtest", fake_rb)
    cs = candles_from([float(i) for i in range(50)])
    def fit(skill, train_candles, cfg):
        fit_calls.append((train_candles[0].time, train_candles[-1].time, len(train_candles)))
        return replace(cfg, fee=0.123)
    walk_forward(MaTrend, cs, CONFIG, 5000.0, train=20, test=10, fit_fn=fit)
    assert len(fit_calls) == 3
    assert all(n == 20 for *_, n in fit_calls)             # fit saw ONLY the 20-candle train slice
    assert fit_calls[0][0] == cs[0].time and fit_calls[0][1] == cs[19].time
    assert all(c.fee == 0.123 for c in eval_cfgs)          # frozen cfg applied to every test eval


# --- the honesty invariant: no lookahead ---
def test_no_lookahead_future_candles_cannot_change_a_fold():
    sawtooth = list(range(50, 10, -1)) + list(range(10, 50))             # 80-candle cycle
    base = [float(x) for x in sawtooth * 3]                              # 240 candles, trades freely
    out1 = walk_forward(RsiRevert, candles_from(base), CONFIG, 5000.0, train=40, test=60, window=50)
    mutated = list(base)
    for i in range(100, len(mutated)):                      # mutate ONLY after fold0's test [40,100)
        mutated[i] = mutated[i] * 2 + 7
    out2 = walk_forward(RsiRevert, candles_from(mutated), CONFIG, 5000.0, train=40, test=60, window=50)
    assert out1["folds"][0]["trades"] > 0                   # not vacuous: fold0 actually traded
    assert out1["folds"][0]["return_pct"] == out2["folds"][0]["return_pct"]   # fold0 untouched
    assert out1["folds"][0]["trades"] == out2["folds"][0]["trades"]
    # sanity: the mutation DID matter for a later fold (so the invariant above is meaningful)
    assert any(out1["folds"][k]["return_pct"] != out2["folds"][k]["return_pct"]
               or out1["folds"][k]["trades"] != out2["folds"][k]["trades"]
               for k in range(1, len(out1["folds"])))


# --- real skill + empty handling ---
def test_walk_forward_real_skill_structure():
    prices = [float(p) for p in (list(range(50, 10, -1)) + list(range(10, 50))
                                 + list(range(50, 10, -1)) + list(range(10, 50)))]
    out = walk_forward(MaTrend, candles_from(prices), CONFIG, 5000.0, train=40, test=20, window=30)
    assert out["strategy"] == "ma_trend"
    assert out["train"] == 40 and out["test"] == 20 and out["step"] == 20
    assert len(out["folds"]) >= 2
    for f in out["folds"]:
        assert {"fold", "train_range", "test_range", "return_pct", "trades"} <= set(f)
    assert {"folds", "compounded_return_pct", "final_equity", "hit_rate", "total_trades",
            "mean_sharpe", "worst_drawdown", "worst_return_pct"} <= set(out["oos"])
    assert out["oos"]["folds"] == len(out["folds"])


def test_walk_forward_too_few_candles_yields_no_folds():
    out = walk_forward(MaTrend, candles_from([float(i) for i in range(20)]), CONFIG, 5000.0,
                       train=30, test=10)
    assert out["folds"] == []
    assert out["oos"]["folds"] == 0
    assert out["oos"]["final_equity"] == 5000.0            # nothing traded -> balance intact
    assert out["oos"]["hit_rate"] == 0.0


def test_bare_instance_rejected_to_prevent_state_bleed():
    # A stateful skill reused across folds would carry learning across the fit->eval boundary, the
    # exact leak this harness prevents — so a non-callable (bare instance) is refused.
    with pytest.raises(TypeError):
        walk_forward(MaTrend(), candles_from([float(i) for i in range(50)]), CONFIG, 5000.0,
                     train=20, test=10)


def test_format_renders_folds_and_oos_and_empty(monkeypatch):
    monkeypatch.setattr(wf, "run_backtest",
                        lambda skill, cands, cfg, bal, window=200: _stub(skill.name, return_pct=3.0))
    cs = candles_from([float(i) for i in range(50)])
    txt = _format(walk_forward(MaTrend, cs, CONFIG, 5000.0, train=20, test=10))
    assert "ma_trend" in txt and "OOS (all):" in txt and "fold" in txt
    empty = _format(walk_forward(MaTrend, candles_from([0.0] * 5), CONFIG, 5000.0, train=20, test=10))
    assert "no folds" in empty
