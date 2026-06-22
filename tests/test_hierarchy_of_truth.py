"""Hierarchy of Truth — the governance invariant that keeps model-authored text out of the
audit-truth tables. See docs/hierarchy-of-truth.md. These tests fail loudly if a future
schema change adds a table without classifying it, or blurs the two classes.
"""
from homing_trade.db import Database, AUDIT_TRUTH_TABLES, MODEL_AUTHORED_TABLES
from homing_trade.selfquery import SelfQuery
from homing_trade.repository import Repository


def test_classes_are_disjoint():
    # A table is EITHER ground truth OR a place for model prose — never both.
    assert AUDIT_TRUTH_TABLES.isdisjoint(MODEL_AUTHORED_TABLES)


def test_every_live_table_is_classified(tmp_path):
    # When someone adds a table to the schema, they must classify it — this fails until they do.
    db = Database(str(tmp_path / "h.db"))
    classified = AUDIT_TRUTH_TABLES | MODEL_AUTHORED_TABLES
    unclassified = db.table_names() - classified
    assert unclassified == set(), f"unclassified tables (classify in db.py): {unclassified}"


def test_ground_truth_tables_are_audit_truth():
    # The immutable financial/market record the roadmap names explicitly.
    for t in ("wallets", "positions", "trades", "equity", "candles"):
        assert t in AUDIT_TRUTH_TABLES


def test_model_authored_set_is_exactly_these():
    # Exactly the tables permitted model-authored free text. proposals joined in Phase 4
    # (it carries the AI's rationale + proposed payload).
    assert MODEL_AUTHORED_TABLES == {"decision_log", "llm_responses", "reflections",
                                     "playbooks", "proposals"}


def test_derived_observability_tables_are_audit_truth():
    # regimes / trade_outcomes / risk_events are computed mechanically, never model-authored.
    for t in ("regimes", "trade_outcomes", "risk_events"):
        assert t in AUDIT_TRUTH_TABLES


class _RecordingRepo:
    """Records every Repository method SelfQuery touches, so we can assert it only reads."""
    READS = {"closed_pnls", "equity_series", "get_balance", "recent_risk_events",
             "taken_action_counts", "trade_outcomes"}

    def __init__(self, real):
        self._real = real
        self.calls = []

    def __getattr__(self, name):
        self.calls.append(name)
        return getattr(self._real, name)


def test_selfquery_never_writes(tmp_path):
    # The read path into the ledger (SelfQuery) can physically only call read methods —
    # it has no way to author a row in any table, audit-truth or otherwise.
    repo = Repository.open(str(tmp_path / "sq.db"))
    repo.ensure_strategy("ma_trend", 5000.0)
    spy = _RecordingRepo(repo)
    sq = SelfQuery(spy, 5000.0)
    sq.performance("ma_trend")
    sq.leaderboard(["ma_trend"])
    sq.risk_event_counts()
    sq.decision_breakdown("ma_trend")
    sq.outcomes()
    sq.regime_performance()
    sq.exit_reason_breakdown()
    sq.directional_accuracy()
    assert spy.calls and set(spy.calls) <= _RecordingRepo.READS
