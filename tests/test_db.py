import pytest
from homing_trade.db import Database, SCHEMA_VERSION
from homing_trade.models import Position


def make_db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def test_ensure_strategy_idempotent(tmp_path):
    db = make_db(tmp_path)
    db.ensure_strategy("ma_trend", 5000.0)
    db.ensure_strategy("ma_trend", 5000.0)  # second call must not duplicate/reset
    assert db.get_balance("ma_trend") == 5000.0


def test_set_and_get_balance(tmp_path):
    db = make_db(tmp_path)
    db.ensure_strategy("rsi_revert", 5000.0)
    db.set_balance("rsi_revert", 4800.0)
    assert db.get_balance("rsi_revert") == 4800.0


def test_open_get_close_position(tmp_path):
    db = make_db(tmp_path)
    db.ensure_strategy("ma_trend", 5000.0)
    pos = Position(strategy="ma_trend", side="LONG", entry_price=100.0, size=0.5,
                   leverage=3.0, margin=50.0, stop_price=98.0, opened_at=1000)
    pid = db.open_position(pos)
    assert isinstance(pid, int)
    fetched = db.get_open_position("ma_trend")
    assert fetched is not None and fetched.id == pid and fetched.side == "LONG"
    db.close_position(pid)
    assert db.get_open_position("ma_trend") is None


def test_state_roundtrip(tmp_path):
    db = make_db(tmp_path)
    assert db.get_state("last_candle_time") is None
    db.set_state("last_candle_time", "1717000000000")
    assert db.get_state("last_candle_time") == "1717000000000"


def test_records_do_not_raise(tmp_path):
    db = make_db(tmp_path)
    db.ensure_strategy("grid", 5000.0)
    db.record_trade("grid", None, "LONG", "OPEN", 100.0, 0.5, 0.05, 0.0, 1000)
    db.record_equity("grid", 5000.0, 1000)
    db.log_decision("grid", 1000, 999, "HOLD", 0.0, "no signal", {"rsi": 55})


def test_schema_version_set_on_init(tmp_path):
    db = make_db(tmp_path)
    assert db.schema_version() == SCHEMA_VERSION


def test_migrations_create_reflection_indexes(tmp_path):
    db = make_db(tmp_path)
    names = {r["name"] for r in db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    for idx in ("idx_decision_log_strategy_ts", "idx_llm_responses_strategy_ts",
                "idx_trades_strategy_ts", "idx_trades_position_id"):
        assert idx in names


def test_migrate_idempotent_on_reopen(tmp_path):
    p = str(tmp_path / "mig.db")
    Database(p).close()                 # first init migrates to head
    db = Database(p)                    # re-open must not error and stays at head
    assert db.schema_version() == SCHEMA_VERSION


def test_migrate_upgrades_legacy_db(tmp_path):
    # Build a genuine pre-v2 DB (base schema, schema_version=1, no v2 columns) and confirm
    # opening it upgrades in place: v2 columns + risk_events get added, version -> head.
    import sqlite3
    from homing_trade.db import SCHEMA
    p = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(p)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO state(key, value) VALUES('schema_version', '1')")
    conn.commit()
    conn.close()
    db = Database(p)                       # __init__ runs _migrate() -> applies v2
    assert db.schema_version() == SCHEMA_VERSION
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(decision_log)")}
    assert "decision_id" in cols and "rejection_rationale" in cols
    tables = {r["name"] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "risk_events" in tables


def test_migrate_skips_already_applied_version(tmp_path, monkeypatch):
    # The `ver > current` guard must NOT re-run an applied migration: swap v1 for SQL
    # that would explode if executed, then confirm _migrate is a clean no-op at head.
    import homing_trade.db as dbmod
    db = make_db(tmp_path)  # already at head
    monkeypatch.setitem(dbmod.MIGRATIONS, 1, ["THIS WOULD FAIL IF EXECUTED"])
    db._migrate()
    assert db.schema_version() == SCHEMA_VERSION


def test_closed_pnls_and_equity_series(tmp_path):
    db = make_db(tmp_path)
    db.ensure_strategy("ma_trend", 5000.0)
    db.record_trade("ma_trend", 1, "LONG", "OPEN", 100.0, 1.0, 0.1, -0.1, 1000)
    db.record_trade("ma_trend", 1, "LONG", "CLOSE", 110.0, 1.0, 0.1, 9.9, 2000)
    db.record_equity("ma_trend", 5010.0, 2000)
    assert db.closed_pnls("ma_trend") == [9.9]      # excludes the OPEN row
    assert db.equity_series("ma_trend") == [5010.0]


def test_migrate_atomic_rolls_back_partial(tmp_path, monkeypatch):
    # A version whose later statement fails must leave NO partial DDL and NOT bump the version.
    # Inject a version ABOVE head so _migrate actually runs it.
    import homing_trade.db as dbmod
    db = make_db(tmp_path)  # at head
    bad_ver = SCHEMA_VERSION + 1
    monkeypatch.setitem(dbmod.MIGRATIONS, bad_ver, [
        "CREATE INDEX IF NOT EXISTS idx_tmp_bad ON trades(fee)",  # would succeed alone
        "CREATE INDEX bad_syntax ON",                            # broken -> rolls back this version
    ])
    with pytest.raises(Exception):
        db._migrate()
    assert db.schema_version() == SCHEMA_VERSION                 # version not bumped
    names = {r["name"] for r in db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "idx_tmp_bad" not in names                            # partial DDL rolled back


def test_migration_v2_adds_columns_and_risk_events(tmp_path):
    db = make_db(tmp_path)
    assert db.schema_version() == SCHEMA_VERSION
    dl = {r[1] for r in db.conn.execute("PRAGMA table_info(decision_log)")}
    assert {"decision_id", "intended_action", "taken_action", "rejection_rationale",
            "regime", "realized_vol", "prompt_version", "playbook_version"} <= dl
    lr = {r[1] for r in db.conn.execute("PRAGMA table_info(llm_responses)")}
    assert {"prompt_version", "prompt_hash", "next_check_in_sec", "requested_charts"} <= lr
    tr = {r[1] for r in db.conn.execute("PRAGMA table_info(trades)")}
    assert {"decision_price", "slippage"} <= tr
    tables = {r["name"] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "risk_events" in tables


def test_risk_events_roundtrip(tmp_path):
    db = make_db(tmp_path)
    db.record_risk_event(1000, "ma_trend", "veto", "daily notional cap", 1234.5)
    db.record_risk_event(2000, None, "halt", "daily loss limit", None)
    evs = db.recent_risk_events(10)
    assert [e["kind"] for e in evs] == ["halt", "veto"]   # newest first
    assert evs[1]["notional"] == 1234.5 and evs[1]["strategy"] == "ma_trend"
