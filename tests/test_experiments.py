"""Phase 7 #4: A/B variant bookkeeping — the experiments ledger + the two-sample stats engine.

Stats: exact Welch two-sided p-values (anchored to textbook t-table values), identical-sample p≈1,
a→b symmetry, separation monotonicity, the >=2-obs guard, and min_detectable_effect. Ledger: the v12
experiments table create→conclude→get/list roundtrip, status derivation, and the search-budget count
(the denominator Phase 7 #5's multiple-comparison correction divides over). All offline, deterministic.
"""
import pytest

from homing_trade.db import Database, AUDIT_TRUTH_TABLES, SCHEMA_VERSION
from homing_trade.experiments import (two_sample_test, t_two_sided_p, welch_t, betai,
                                      min_detectable_effect)


# --- stats: exact p-value anchors (df=10 t-table) ---
def test_t_two_sided_p_matches_t_table():
    assert t_two_sided_p(2.0, 10) == pytest.approx(0.0734, abs=1e-3)        # textbook
    assert t_two_sided_p(2.228139, 10) == pytest.approx(0.05, abs=2e-3)     # 5% critical value
    assert t_two_sided_p(0.0, 10) == pytest.approx(1.0)                     # no difference
    assert t_two_sided_p(3.169273, 10) == pytest.approx(0.01, abs=2e-3)     # 1% critical value


def test_betai_endpoints_and_symmetry():
    assert betai(2.0, 3.0, 0.0) == 0.0 and betai(2.0, 3.0, 1.0) == 1.0
    # I_x(a,b) = 1 - I_{1-x}(b,a)
    assert betai(2.0, 5.0, 0.3) == pytest.approx(1.0 - betai(5.0, 2.0, 0.7), abs=1e-12)


def test_two_sample_identical_means_p_near_one():
    r = two_sample_test([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])
    assert r["diff"] == 0.0 and r["p_value"] == pytest.approx(1.0)


def test_two_sample_clear_separation_is_significant():
    r = two_sample_test([1, 2, 3, 4, 5], [6, 7, 8, 9, 10])
    assert r["t"] == pytest.approx(-5.0) and r["df"] == pytest.approx(8.0)
    assert r["p_value"] < 0.01


def test_two_sample_is_symmetric_in_p():
    a, b = [1.0, 2.0, 3.0, 4.0, 7.0], [2.0, 3.0, 5.0, 8.0, 9.0]
    assert two_sample_test(a, b)["p_value"] == pytest.approx(two_sample_test(b, a)["p_value"])


def test_p_value_decreases_as_separation_grows():
    base = [1.0, 2.0, 3.0, 4.0, 5.0]
    near = two_sample_test(base, [2.0, 3.0, 4.0, 5.0, 6.0])["p_value"]
    far = two_sample_test(base, [9.0, 10.0, 11.0, 12.0, 13.0])["p_value"]
    assert far < near


def test_two_sample_needs_two_observations():
    with pytest.raises(ValueError):
        two_sample_test([1.0], [1.0, 2.0])


def test_welch_handles_flat_samples():
    t, df = welch_t([5.0, 5.0, 5.0], [5.0, 5.0, 5.0])
    assert t == 0.0 and df >= 1


def test_min_detectable_effect():
    mde = min_detectable_effect(50, 50, 2.0)
    assert mde == pytest.approx((1.959964 + 0.841621) * 2.0 * (2 / 50) ** 0.5, rel=1e-9)
    assert min_detectable_effect(0, 50, 2.0) == float("inf")    # undefined with no data
    assert min_detectable_effect(50, 50, 0.0) == float("inf")   # undefined with no variance
    # more samples -> smaller detectable effect
    assert min_detectable_effect(500, 500, 2.0) < min_detectable_effect(50, 50, 2.0)


# --- experiments ledger (v12) ---
def test_schema_at_v12_and_experiments_is_audit_truth():
    assert SCHEMA_VERSION >= 12
    assert "experiments" in AUDIT_TRUTH_TABLES


def test_experiment_create_conclude_roundtrip(tmp_path):
    db = Database(str(tmp_path / "e.db"))
    eid = db.create_experiment("supertrend>ma_trend on pnl_pct", "supertrend", "ma_trend",
                               "pnl_pct", start_ts=1000, mde=0.5, correction_method="bonferroni")
    e = db.get_experiment(eid)
    assert e["status"] == "running" and e["result"] is None and e["end_ts"] is None
    assert e["variant_a"] == "supertrend" and e["mde"] == 0.5
    db.conclude_experiment(eid, end_ts=5000, n_a=40, n_b=42, result="a_wins", p_value=0.012)
    e2 = db.get_experiment(eid)
    assert e2["status"] == "concluded" and e2["result"] == "a_wins"
    assert e2["n_a"] == 40 and e2["p_value"] == pytest.approx(0.012)
    assert e2["correction_method"] == "bonferroni"   # preserved when not overridden
    db.close()


def test_conclude_can_override_correction_method(tmp_path):
    db = Database(str(tmp_path / "e.db"))
    eid = db.create_experiment("h", "a", "b", "pnl_pct", start_ts=1000)
    db.conclude_experiment(eid, 2000, 10, 10, "inconclusive", 0.4, correction_method="bh")
    assert db.get_experiment(eid)["correction_method"] == "bh"
    db.close()


def test_list_and_search_budget(tmp_path):
    db = Database(str(tmp_path / "e.db"))
    db.create_experiment("h1", "a", "b", "pnl_pct", start_ts=1000)
    e2 = db.create_experiment("h2", "a", "c", "pnl_pct", start_ts=2000)
    db.create_experiment("h3", "b", "c", "pnl_pct", start_ts=9000)
    db.conclude_experiment(e2, 3000, 5, 5, "b_wins", 0.03)
    assert len(db.list_experiments()) == 3
    assert len(db.list_experiments(status="running")) == 2
    assert [e["id"] for e in db.list_experiments(status="concluded")] == [e2]
    # search budget = experiments STARTED in the window (multiple-testing denominator)
    assert db.experiment_search_budget(0, 10000) == 3
    assert db.experiment_search_budget(0, 2500) == 2
    assert db.experiment_search_budget(5000, 10000) == 1
    db.close()


def test_concluded_status_keys_off_end_ts_not_result(tmp_path):
    # a conclusion with a NULL/'inconclusive' result is still CONCLUDED (status tracks end_ts)
    db = Database(str(tmp_path / "e.db"))
    eid = db.create_experiment("h", "a", "b", "pnl_pct", start_ts=1000)
    db.conclude_experiment(eid, end_ts=2000, n_a=5, n_b=5, result=None, p_value=0.4)
    assert db.get_experiment(eid)["status"] == "concluded"
    db.close()


def test_get_missing_experiment_is_none(tmp_path):
    db = Database(str(tmp_path / "e.db"))
    assert db.get_experiment(999) is None
    db.close()
