"""Phase 7 #6: the profit-mirage trust guard.

A backtest is trusted only when its evaluation window is entirely AFTER the model's knowledge cutoff
AND walk-forward. Covers ISO→ms parsing, assess_window's two conditions, fold partitioning, the
walk-forward harness's per-fold post_cutoff tag + trusted_oos aggregate, the promotion gate refusing
pre-cutoff evidence, and that the cutoff config field is protected from model-authored proposals."""
import pytest

from homing_trade.profit_mirage import (cutoff_ms_from_iso, is_post_cutoff, assess_window,
                                        partition_folds_by_trust)
from homing_trade.walkforward import walk_forward
from homing_trade.config import CONFIG, Config
from homing_trade.skills.ma_trend import MaTrend
from homing_trade.models import Candle


def candles_from(prices, t0=0, dt=60000):
    return [Candle(open=p, high=p + 1, low=p - 1, close=p, volume=1.0, time=t0 + i * dt)
            for i, p in enumerate(prices)]


# --- cutoff parsing ---
def test_cutoff_parsing_date_and_datetime_and_blank():
    a = cutoff_ms_from_iso("2026-01-01")
    b = cutoff_ms_from_iso("2026-01-01T00:00:00Z")
    assert a == b == 1767225600000           # 2026-01-01T00:00:00Z in ms
    assert cutoff_ms_from_iso("") is None and cutoff_ms_from_iso("  ") is None   # intentional disable


def test_invalid_cutoff_fails_closed():
    # a non-blank but unparseable cutoff must RAISE, never silently disable the guard (fail-closed)
    with pytest.raises(ValueError):
        cutoff_ms_from_iso("not-a-date")
    with pytest.raises(ValueError):
        cutoff_ms_from_iso("2026-13-99")


def test_is_post_cutoff_and_none_means_no_constraint():
    c = cutoff_ms_from_iso("2026-01-01")
    assert is_post_cutoff(c + 1, c) and is_post_cutoff(c, c)
    assert not is_post_cutoff(c - 1, c)
    assert is_post_cutoff(0, None)           # no cutoff -> everything passes


# --- assess_window ---
def test_assess_window_requires_post_cutoff_and_walk_forward():
    c = 1_000_000
    assert assess_window(c + 5, c + 99, cutoff_ms=c)["trusted"] is True
    pre = assess_window(c - 5, c + 99, cutoff_ms=c)
    assert pre["trusted"] is False and pre["post_cutoff"] is False and "predates" in pre["reason"]
    insample = assess_window(c + 5, c + 99, cutoff_ms=c, walk_forward=False)
    assert insample["trusted"] is False and "in-sample" in insample["reason"]
    # malformed window (end before start) -> untrusted, never silently trusted
    bad = assess_window(c + 99, c + 5, cutoff_ms=c)
    assert bad["trusted"] is False and "malformed" in bad["reason"]


def test_partition_folds_by_trust():
    c = 1000
    folds = [{"fold": 0, "test_range": (500, 800)}, {"fold": 1, "test_range": (1000, 1500)},
             {"fold": 2, "test_range": (2000, 2500)}, {"fold": 3}]  # missing range -> untrusted
    trusted, untrusted = partition_folds_by_trust(folds, c)
    assert [f["fold"] for f in trusted] == [1, 2]
    assert [f["fold"] for f in untrusted] == [0, 3]


# --- walk-forward harness annotation ---
def test_walk_forward_tags_folds_and_trusted_oos():
    # 5 folds; place the cutoff so only the later folds' test windows are post-cutoff
    cs = candles_from([float(x) for x in range(200)], t0=0, dt=1000)   # times 0..199000
    out = walk_forward(MaTrend, cs, CONFIG, 5000.0, train=20, test=20, window=15,
                       cutoff_ms=cs[100].time)
    assert "trusted_oos" in out and out["cutoff_ms"] == cs[100].time
    for f in out["folds"]:
        assert f["post_cutoff"] == (f["test_range"][0] >= cs[100].time)
    trusted = [f for f in out["folds"] if f["post_cutoff"]]
    assert out["trusted_oos"]["folds"] == len(trusted) < out["oos"]["folds"]   # strictly fewer


def test_walk_forward_no_cutoff_trusts_all():
    cs = candles_from([float(x) for x in range(120)], dt=1000)
    out = walk_forward(MaTrend, cs, CONFIG, 5000.0, train=20, test=20, window=15)
    assert out["cutoff_ms"] is None
    assert all(f["post_cutoff"] for f in out["folds"])              # None cutoff -> all trusted
    assert out["trusted_oos"]["folds"] == out["oos"]["folds"]


# --- promotion gate honours the cutoff ---
def _samples(mean, n=40):
    return [mean + (i % 5) * 0.05 - 0.1 for i in range(n)]


def test_promotion_refuses_pre_cutoff_window(tmp_path):
    from homing_trade.repository import Repository
    from homing_trade.promotion import evaluate_and_maybe_promote
    repo = Repository.open(str(tmp_path / "p.db"))
    cutoff = 1_000_000
    eid = repo.create_experiment("h", "supertrend", "ma_trend", "pnl_pct", start_ts=100, mde=0.1)
    # strong edge, but the OOS window predates the cutoff -> must NOT promote
    v = evaluate_and_maybe_promote(repo, eid, _samples(1.5), _samples(0.2), now_ms=5000,
                                   oos_window=(cutoff - 5000, cutoff - 10), cutoff_ms=cutoff)
    assert not v["promote"] and v["trusted"] is False and v["proposal_id"] is None
    assert "predates" in v["reason"]
    assert repo.get_experiment(eid)["result"] == "untrusted"
    repo.close()


def test_promotion_allows_post_cutoff_window(tmp_path):
    from homing_trade.repository import Repository
    from homing_trade.promotion import evaluate_and_maybe_promote
    repo = Repository.open(str(tmp_path / "p.db"))
    cutoff = 1_000_000
    eid = repo.create_experiment("h", "supertrend", "ma_trend", "pnl_pct", start_ts=100, mde=0.1)
    v = evaluate_and_maybe_promote(repo, eid, _samples(1.5), _samples(0.2), now_ms=5000,
                                   oos_window=(cutoff + 10, cutoff + 5000), cutoff_ms=cutoff)
    assert v["promote"] and v["proposal_id"] is not None
    repo.close()


# --- CLI formatting surfaces the trusted subset ---
def test_format_shows_trusted_line_both_branches():
    from homing_trade.walkforward import _format
    cs = candles_from([float(x) for x in range(200)], t0=0, dt=1000)
    # mixed: cutoff mid-series -> some post-cutoff folds -> "post-cutoff N/M" line
    mixed = _format(walk_forward(MaTrend, cs, CONFIG, 5000.0, train=20, test=20, window=15,
                                 cutoff_ms=cs[100].time))
    assert "TRUSTED (post-cutoff" in mixed
    # cutoff beyond all data -> no post-cutoff folds -> the mirage-risk line
    none_trusted = _format(walk_forward(MaTrend, cs, CONFIG, 5000.0, train=20, test=20, window=15,
                                        cutoff_ms=cs[-1].time + 10**9))
    assert "no post-cutoff folds" in none_trusted


# --- config safety ---
def test_trust_cutoff_default_and_protected():
    assert Config().trust_cutoff_iso == "2026-01-01"
    from homing_trade.db import _is_protected
    assert _is_protected("trust_cutoff_iso")
