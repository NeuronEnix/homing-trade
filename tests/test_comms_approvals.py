"""Phase 3 #8: Discord #comms replies → proposal approvals.

Covers the pure command parser, the approve/reject/status handler against the real approval gate,
and the self-gated polling runner (cursor advance, exactly-once, never raises)."""
import pytest

from homing_trade import comms, comms_approvals as ca
from homing_trade.db import Database
from homing_trade.config import Config


# --- the parser ------------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("approve 7", ("approve", 7)),
    ("/approve #7", ("approve", 7)),
    ("APPROVE proposal 12", ("approve", 12)),
    ("reject 9", ("reject", 9)),
    ("rejected 9", ("reject", 9)),
    ("approve", ("approve", None)),         # keyword, no id
    ("status", ("status", None)),
    ("pending", ("status", None)),
    ("just chatting about approve 5", None),  # keyword NOT first token -> not a command
    ("I don't approve of that", None),
    ("ok lets go with 3", None),            # casual affirmative is NOT approve (explicit verb only)
    ("yes 7 times", None),
    ("no way 3", None),
    ("👍 5", None),
    ("", None),
    ("   ", None),
    (None, None),
])
def test_parse_command(text, expected):
    assert ca.parse_command(text) == expected


# --- the handler against the real gate -------------------------------------------------------
@pytest.fixture
def repo(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    yield db
    db.close()


def test_approve_a_param_proposal_decides_but_notes_not_auto_applied(repo):
    pid = repo.create_proposal("rsi_revert", "param", {"rsi_period": 9}, "faster", created_ts=1)
    reply = ca.apply_command(repo, ("approve", pid), now_ms=1000)
    assert f"#{pid} approved" in reply and "not auto-applied" in reply
    assert repo.get_proposal(pid)["status"] == "approved"      # the human gate flipped


def test_reject_marks_rejected(repo):
    pid = repo.create_proposal("rsi_revert", "param", {"rsi_period": 9}, "x", created_ts=1)
    reply = ca.apply_command(repo, ("reject", pid), now_ms=1000)
    assert f"#{pid} rejected" in reply
    assert repo.get_proposal(pid)["status"] == "rejected"


def test_decide_unknown_or_already_decided_is_reported_not_crashed(repo):
    assert "not pending" in ca.apply_command(repo, ("approve", 999), now_ms=1)
    pid = repo.create_proposal("rsi_revert", "param", {"rsi_period": 9}, "x", created_ts=1)
    repo.decide_proposal(pid, "approved", "human:web", 1)
    assert "not pending" in ca.apply_command(repo, ("reject", pid), now_ms=2)   # already decided


def test_missing_id_prompts_for_one(repo):
    assert "approve <id>" in ca.apply_command(repo, ("approve", None), now_ms=1)


def test_status_lists_pending(repo):
    repo.create_proposal("rsi_revert", "param", {"rsi_period": 9}, "shorter window", created_ts=1)
    reply = ca.apply_command(repo, ("status", None), now_ms=1)
    assert "1 pending" in reply and "shorter window" in reply
    # with nothing pending
    repo2_empty = ca.apply_command(repo, ("status", None), now_ms=1)  # still 1 pending here
    assert "pending" in repo2_empty


# --- the polling runner ----------------------------------------------------------------------
def test_poll_acts_on_commands_advances_cursor_and_is_exactly_once(repo):
    pid = repo.create_proposal("rsi_revert", "param", {"rsi_period": 9}, "x", created_ts=1)
    posted = []
    # reader respects the cursor: returns the messages only on the first poll (after_id is None)
    def reader(after_id=None):
        return [] if after_id else [
            {"id": "100", "author": "krb", "content": "chatter, no command"},
            {"id": "101", "author": "krb", "content": f"approve {pid}"},
        ]
    runner = ca.CommsApprovalRunner(repo, Config(), reader=reader, poster=lambda t: posted.append(t))
    assert runner.poll_once(now_ms=1000) == 1                  # one command acted on
    assert repo.get_proposal(pid)["status"] == "approved"
    assert repo.get_state("comms_after_id") == "101"          # cursor advanced past ALL messages
    assert len(posted) == 1
    # a second poll sees nothing new (cursor in place) -> no double-decide
    assert runner.poll_once(now_ms=2000) == 0


def test_run_is_off_by_default_no_network(repo):
    # Config default comms_inbound_enabled=False -> the reader is NEVER called (keeps tests offline
    # and makes inbound a deliberate opt-in).
    calls = []
    runner = ca.CommsApprovalRunner(repo, Config(),
                                    reader=lambda after_id=None: calls.append(1) or [],
                                    poster=lambda t: None, clock=lambda: 10**9)
    assert runner.run() == 0 and calls == []


def test_run_self_gates_on_cadence_and_inbound(monkeypatch, repo):
    monkeypatch.setattr(comms, "inbound_enabled", lambda **k: True)
    cfg = Config(comms_inbound_enabled=True, comms_poll_sec=30)
    calls = []
    runner = ca.CommsApprovalRunner(repo, cfg, reader=lambda after_id=None: calls.append(1) or [],
                                    poster=lambda t: None, clock=lambda: 1_000_000)
    runner.run()                                              # first call polls
    assert calls == [1]
    runner.run()                                              # within the cadence window -> skip
    assert calls == [1]
    # inbound creds absent -> never polls even when enabled
    monkeypatch.setattr(comms, "inbound_enabled", lambda **k: False)
    runner._last_poll_ms = 0
    runner.run()
    assert calls == [1]


def test_run_never_raises_into_the_trading_loop(monkeypatch, repo):
    monkeypatch.setattr(comms, "inbound_enabled", lambda **k: True)
    def boom(after_id=None):
        raise RuntimeError("discord 500")
    runner = ca.CommsApprovalRunner(repo, Config(comms_inbound_enabled=True),
                                    reader=boom, poster=lambda t: None, clock=lambda: 10**9)
    assert runner.run() == 0                                   # reader raised -> swallowed, no raise
