"""Phase-4 store: reflections (batched/per-trade retrospection) + append-only playbooks."""
import json
from homing_trade.db import Database


def make(tmp_path):
    db = Database(str(tmp_path / "p4.db"))
    db.ensure_strategy("ma", 5000.0)
    return db


def test_tables_exist_after_migration(tmp_path):
    db = make(tmp_path)
    assert {"reflections", "playbooks"} <= db.table_names()


def test_record_and_read_reflections_newest_first(tmp_path):
    db = make(tmp_path)
    db.record_reflection("ma", "periodic", 1000, batch_from_ts=0, batch_to_ts=1000,
                         trade_ids=[1, 2, 3], metrics={"win_rate": 0.66},
                         lesson="stops too tight in chop", new_playbook_version="ma-v2",
                         model="claude-opus-4-8", raw="{}")
    db.record_reflection("ma", "per_trade", 2000, trade_ids=[4], lesson="late entry")
    rows = db.recent_reflections("ma")
    assert [r["ts"] for r in rows] == [2000, 1000]              # newest first
    r0 = rows[1]
    assert json.loads(r0["trade_ids_json"]) == [1, 2, 3]       # JSON round-trips
    assert json.loads(r0["metrics_json"])["win_rate"] == 0.66
    assert r0["lesson"] == "stops too tight in chop" and r0["new_playbook_version"] == "ma-v2"


def test_reflections_filter_by_strategy(tmp_path):
    db = make(tmp_path)
    db.ensure_strategy("grid", 5000.0)
    db.record_reflection("ma", "periodic", 1000, lesson="x")
    db.record_reflection("grid", "periodic", 1000, lesson="y")
    assert len(db.recent_reflections("ma")) == 1
    assert len(db.recent_reflections()) == 2                   # all strategies


def test_playbook_publish_latest_and_retire(tmp_path):
    db = make(tmp_path)
    db.publish_playbook("ma-v1", "ma", 1000, {"rules": ["trend only"]})
    db.publish_playbook("ma-v2", "ma", 2000, {"rules": ["trend only", "skip chop"]},
                        parent_version="ma-v1")
    latest = db.latest_playbook("ma")
    assert latest["version"] == "ma-v2"                        # newest non-retired
    assert json.loads(latest["rules_json"])["rules"] == ["trend only", "skip chop"]
    assert latest["parent_version"] == "ma-v1"
    # retiring v2 falls back to v1; retire only sets retired_ts, never rules
    db.retire_playbook("ma-v2", 3000)
    assert db.latest_playbook("ma")["version"] == "ma-v1"
    assert db.get_playbook("ma-v2")["retired_ts"] == 3000
    assert json.loads(db.get_playbook("ma-v2")["rules_json"])["rules"] == ["trend only", "skip chop"]


def test_latest_playbook_none_when_absent(tmp_path):
    assert make(tmp_path).latest_playbook("ma") is None
