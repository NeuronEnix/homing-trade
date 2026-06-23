"""Phase 9 #4: structured, verified provenance for self-mod PRs.

Covers the pure builder (validation + formatting) and the DB round-trip (resolve a real
reflections/proposals row into a verified Provenance, record the reverse link, query it back).
A provenance can only point at a model-authored source table and at a row that actually exists —
a self-mod PR can never carry a fabricated or dangling reference."""
import pytest

from homing_trade import provenance as prov
from homing_trade.db import Database, MODEL_AUTHORED_TABLES


# --- the pure builder -------------------------------------------------------------------------
def test_make_provenance_ref_and_str():
    p = prov.make_provenance("reflections", 42, "cut RSI period; whipsaws in chop")
    assert p.ref == "reflections#42"
    assert str(p) == 'reflections#42 — "cut RSI period; whipsaws in chop"'


def test_make_provenance_without_summary_is_bare_ref():
    assert str(prov.make_provenance("proposals", 7)) == "proposals#7"


def test_summary_is_whitespace_collapsed_and_clipped():
    p = prov.make_provenance("reflections", 1, "  too   wide\n\nlesson  ")
    assert "  " not in p.summary and "\n" not in p.summary
    long = prov.make_provenance("reflections", 1, "x" * 500)
    assert len(long.summary) <= 240 and long.summary.endswith("…")


@pytest.mark.parametrize("table", ["", "candles", "equity", "self_mod_prs", "trades", None])
def test_rejects_non_source_table(table):
    # only the model-authored source tables may motivate a change; audit-truth tables cannot
    with pytest.raises(ValueError):
        prov.make_provenance(table, 1)


@pytest.mark.parametrize("rid", [0, -1, 1.5, "3", True, False, None])
def test_rejects_bad_row_id(rid):
    with pytest.raises(ValueError):
        prov.make_provenance("reflections", rid)


def test_source_tables_are_a_subset_of_model_authored():
    # provenance may only point at tables that legitimately carry a motivating lesson/rationale
    assert set(prov.SOURCE_TABLES) <= MODEL_AUTHORED_TABLES


# --- the DB round-trip ------------------------------------------------------------------------
@pytest.fixture
def repo(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    yield db
    db.close()


def test_resolve_provenance_pulls_lesson_from_reflections(repo):
    rid = repo.record_reflection(strategy="rsi_revert", kind="periodic", ts=1000,
                                 lesson="widen the stop in high vol")
    p = repo.resolve_provenance("reflections", rid)
    assert p.table == "reflections" and p.row_id == rid
    assert p.summary == "widen the stop in high vol"


def test_resolve_provenance_pulls_rationale_from_proposals(repo):
    pid = repo.create_proposal("rsi_revert", "param", {"rsi_period": 9},
                               "shorter period reacts faster", created_ts=1000)
    p = repo.resolve_provenance("proposals", pid)
    assert p.ref == f"proposals#{pid}" and p.summary == "shorter period reacts faster"


def test_resolve_provenance_missing_row_fails_closed(repo):
    with pytest.raises(LookupError):
        repo.resolve_provenance("reflections", 999)


def test_resolve_provenance_rejects_non_source_table(repo):
    with pytest.raises(ValueError):
        repo.resolve_provenance("trades", 1)


def test_record_and_query_reverse_link(repo):
    rid = repo.record_reflection(strategy="macd", kind="periodic", ts=1000, lesson="tune signal")
    p = repo.resolve_provenance("reflections", rid)
    repo.record_self_mod_pr(p, "https://github.com/x/y/pull/99", branch="self/tune-macd",
                            title="tune macd", now_ms=2000)
    links = repo.self_mod_prs_for("reflections", rid)
    assert len(links) == 1
    assert links[0]["pr_url"].endswith("/pull/99")
    assert links[0]["branch"] == "self/tune-macd" and links[0]["source_id"] == rid


def test_record_self_mod_pr_fails_closed_on_dangling_source(repo):
    # a hand-built / stale Provenance pointing at a row that doesn't exist must NOT write a
    # dangling back-reference into the ledger
    dangling = prov.make_provenance("reflections", 999)
    with pytest.raises(LookupError):
        repo.record_self_mod_pr(dangling, "url", branch="self/x", title="x", now_ms=1)
    assert repo.self_mod_prs_for("reflections", 999) == []


def test_reverse_link_is_scoped_to_its_source_row(repo):
    a = repo.record_reflection(strategy="macd", kind="periodic", ts=1, lesson="a")
    b = repo.record_reflection(strategy="macd", kind="periodic", ts=2, lesson="b")
    repo.record_self_mod_pr(repo.resolve_provenance("reflections", a),
                            "url-a", branch="self/a", title="a", now_ms=10)
    assert repo.self_mod_prs_for("reflections", b) == []
    assert len(repo.self_mod_prs_for("reflections", a)) == 1


def test_self_mod_prs_is_audit_truth_and_classified(repo):
    from homing_trade.db import AUDIT_TRUTH_TABLES, MODEL_AUTHORED_TABLES
    assert "self_mod_prs" in AUDIT_TRUTH_TABLES
    assert "self_mod_prs" not in MODEL_AUTHORED_TABLES
    assert "self_mod_prs" in repo.table_names()        # migration actually created it
