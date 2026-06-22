"""Phase-4 #7: the APPLY step — where a human-approved proposal becomes a real change.

The gate is strict: only an APPROVED, not-yet-applied proposal applies; the protected-fields
guard is RE-ASSERTED at apply time (defense in depth, even though create_proposal already
blocks them); application is idempotent and records provenance (applied_ts/by/result). The
playbook path publishes the new immutable version and supersedes the current one — which is
exactly what llm_trader injects (Phase-4 #6), closing the learn->correct loop end-to-end.
Nothing risk-/secret-/live-related can ever be applied.
"""
import json
import pytest
from homing_trade.repository import Repository
from homing_trade.proposals import ProposalApplier, ProposalApplyError


def setup(tmp_path):
    repo = Repository.open(str(tmp_path / "a.db"))
    repo.ensure_strategy("ma", 5000.0)
    return repo, ProposalApplier(repo)


def approved_playbook_proposal(repo, version="ma-v1", rules=("trend only", "skip chop"),
                               parent_version=None, decided_ts=2000):
    pid = repo.create_proposal("ma", "playbook",
                               {"version": version, "rules": list(rules),
                                "parent_version": parent_version},
                               "reflection-proposed", 1000)
    repo.decide_proposal(pid, "approved", "human:web", decided_ts)
    return pid


def test_apply_playbook_publishes_new_active_version(tmp_path):
    repo, applier = setup(tmp_path)
    pid = approved_playbook_proposal(repo, version="ma-v1", rules=["trend only", "skip chop"])
    result = applier.apply(pid, applied_by="human:web", now_ms=3000)
    assert result == "ma-v1"
    latest = repo.latest_playbook("ma")
    assert latest["version"] == "ma-v1"
    assert json.loads(latest["rules_json"])["rules"] == ["trend only", "skip chop"]
    p = repo.get_proposal(pid)
    assert p["applied_ts"] == 3000 and p["applied_by"] == "human:web"
    assert p["applied_result"] == "ma-v1"
    repo.close()


def test_apply_playbook_supersedes_the_current_active_version(tmp_path):
    repo, applier = setup(tmp_path)
    repo.publish_playbook("ma-v0", "ma", 500, {"rules": ["old rule"]})   # an existing active one
    pid = approved_playbook_proposal(repo, version="ma-v1", rules=["new rule"])
    applier.apply(pid, applied_by="human:web", now_ms=3000)
    # exactly the new version is active; the old one is retired with true lineage recorded
    assert repo.latest_playbook("ma")["version"] == "ma-v1"
    assert repo.get_playbook("ma-v0")["retired_ts"] == 3000
    assert repo.get_playbook("ma-v1")["parent_version"] == "ma-v0"   # superseded what was current
    repo.close()


def test_apply_requires_approved_status(tmp_path):
    repo, applier = setup(tmp_path)
    pid = repo.create_proposal("ma", "playbook", {"version": "ma-v1", "rules": ["x"]}, "why", 1000)
    with pytest.raises(ProposalApplyError):           # still pending
        applier.apply(pid, applied_by="h", now_ms=3000)
    repo.decide_proposal(pid, "rejected", "h", 2000)
    with pytest.raises(ProposalApplyError):           # rejected
        applier.apply(pid, applied_by="h", now_ms=3000)
    assert repo.latest_playbook("ma") is None         # nothing published
    repo.close()


def test_apply_is_idempotent(tmp_path):
    repo, applier = setup(tmp_path)
    pid = approved_playbook_proposal(repo, version="ma-v1", rules=["r"])
    assert applier.apply(pid, applied_by="h", now_ms=3000) == "ma-v1"
    with pytest.raises(ProposalApplyError):           # second apply refused
        applier.apply(pid, applied_by="h", now_ms=4000)
    # only one version published, applied_ts unchanged from the first apply
    assert repo.get_proposal(pid)["applied_ts"] == 3000
    repo.close()


def test_apply_unknown_proposal_raises(tmp_path):
    repo, applier = setup(tmp_path)
    with pytest.raises(ProposalApplyError):
        applier.apply(999, applied_by="h", now_ms=3000)
    repo.close()


def test_apply_unsupported_kind_raises_and_changes_nothing(tmp_path):
    repo, applier = setup(tmp_path)
    pid = repo.create_proposal("ma", "param", {"ema_period": 34}, "tune", 1000)
    repo.decide_proposal(pid, "approved", "h", 2000)
    with pytest.raises(ProposalApplyError):           # param apply not wired yet
        applier.apply(pid, applied_by="h", now_ms=3000)
    assert repo.get_proposal(pid)["applied_ts"] is None   # not marked applied
    repo.close()


def test_apply_reasserts_protected_guard_defense_in_depth(tmp_path):
    # Even if an approved row carries a protected field (e.g. inserted out-of-band, bypassing
    # create_proposal's guard), apply must REFUSE and change nothing.
    repo, _ = setup(tmp_path)
    applier = ProposalApplier(repo)
    repo.db.conn.execute(
        "INSERT INTO proposals(strategy, kind, payload_json, rationale, status, created_ts) "
        "VALUES('ma','playbook',?,'sneaky','approved',1000)",
        (json.dumps({"version": "ma-v1", "rules": ["x"], "leverage": 50}),))
    repo.db.conn.commit()
    pid = repo.recent_proposals("ma")[0]["id"]
    with pytest.raises(ValueError, match="protected"):
        applier.apply(pid, applied_by="h", now_ms=3000)
    assert repo.latest_playbook("ma") is None         # nothing published
    assert repo.get_proposal(pid)["applied_ts"] is None
    repo.close()


def test_apply_playbook_rejects_empty_or_malformed_payload(tmp_path):
    repo, applier = setup(tmp_path)
    pid = repo.create_proposal("ma", "playbook", {"version": "ma-v1", "rules": []}, "why", 1000)
    repo.decide_proposal(pid, "approved", "h", 2000)
    with pytest.raises(ProposalApplyError):           # empty rules -> not a real change
        applier.apply(pid, applied_by="h", now_ms=3000)
    assert repo.latest_playbook("ma") is None
    repo.close()


def test_apply_playbook_filters_non_string_rules_before_publishing(tmp_path):
    repo, applier = setup(tmp_path)
    pid = repo.create_proposal("ma", "playbook",
                               {"version": "ma-v1", "rules": ["keep", 5, "  ", None, " also "]},
                               "why", 1000)
    repo.decide_proposal(pid, "approved", "h", 2000)
    applier.apply(pid, applied_by="h", now_ms=3000)
    assert json.loads(repo.latest_playbook("ma")["rules_json"])["rules"] == ["keep", "also"]
    repo.close()


def test_apply_playbook_with_null_strategy_refused_clearly(tmp_path):
    # proposals.strategy is nullable but playbooks.strategy is NOT NULL. A strategy-less playbook
    # proposal must be refused with a CLEAR error, not a misleading "version already exists".
    repo, applier = setup(tmp_path)
    repo.db.conn.execute(
        "INSERT INTO proposals(strategy, kind, payload_json, rationale, status, created_ts) "
        "VALUES(NULL,'playbook',?,'x','approved',1000)",
        (json.dumps({"version": "ma-v1", "rules": ["r"]}),))
    repo.db.conn.commit()
    pid = repo.recent_proposals()[0]["id"]
    with pytest.raises(ProposalApplyError, match="no strategy"):
        applier.apply(pid, applied_by="h", now_ms=3000)
    assert repo.get_playbook("ma-v1") is None              # nothing published
    assert repo.get_proposal(pid)["applied_ts"] is None
    repo.close()


def test_duplicate_version_is_atomic_no_partial_state(tmp_path):
    # If the proposed version already exists, apply must refuse and leave EVERYTHING untouched:
    # the existing version not retired, the proposal not marked, no new row. (Atomic apply.)
    repo, applier = setup(tmp_path)
    repo.publish_playbook("dup", "ma", 500, {"rules": ["pre-existing"]})   # collides on version
    pid = approved_playbook_proposal(repo, version="dup", rules=["new"])
    with pytest.raises(ProposalApplyError, match="already exists"):
        applier.apply(pid, applied_by="h", now_ms=3000)
    pbk = repo.get_playbook("dup")
    assert json.loads(pbk["rules_json"])["rules"] == ["pre-existing"]      # untouched
    assert pbk["retired_ts"] is None                                       # not retired
    assert repo.get_proposal(pid)["applied_ts"] is None                    # not marked
    repo.close()
