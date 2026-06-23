"""Backlog: the deterministic decision/candle replay/audit tool.

Covers the pure join (correlate), deterministic chronological ordering, the exact-key join semantics
(llm by strategy+ts, trades/outcome by decision_id, risk veto by strategy+ts), blocked/vetoed
detection, rendering, and a DB round-trip through the repository."""
import pytest

from homing_trade import replay
from homing_trade.db import Database
from homing_trade.repository import Repository


# --- the pure join ---------------------------------------------------------------------------
def test_correlate_orders_by_ts_then_id():
    decisions = [
        {"id": 2, "strategy": "s", "ts": 200, "action": "HOLD"},
        {"id": 1, "strategy": "s", "ts": 100, "action": "LONG"},
        {"id": 3, "strategy": "s", "ts": 100, "action": "SHORT"},   # same ts -> id breaks tie
    ]
    steps = replay.correlate(decisions)
    assert [s.decision["id"] for s in steps] == [1, 3, 2]


def test_llm_joins_on_strategy_and_exact_ts_highest_id_wins():
    decisions = [{"id": 1, "strategy": "ai", "ts": 100, "action": "LONG"}]
    llm = [
        {"id": 5, "strategy": "ai", "ts": 100, "prediction": "up"},
        {"id": 9, "strategy": "ai", "ts": 100, "prediction": "up-final"},  # highest id wins
        {"id": 7, "strategy": "ai", "ts": 999, "prediction": "other-tick"},
        {"id": 8, "strategy": "other", "ts": 100, "prediction": "wrong-strategy"},
    ]
    [step] = replay.correlate(decisions, llm_responses=llm)
    assert step.llm["prediction"] == "up-final"


def test_trades_and_outcome_join_on_decision_id():
    decisions = [{"id": 1, "strategy": "s", "ts": 100, "action": "LONG", "decision_id": "D1"}]
    trades = [
        {"id": 10, "decision_id": "D1", "ts": 100, "action": "OPEN", "side": "LONG"},
        {"id": 11, "decision_id": "OTHER", "ts": 100, "action": "OPEN"},   # different decision
    ]
    outcomes = [{"decision_id": "D1", "realized_pnl": 12.5}, {"decision_id": "X", "realized_pnl": 0}]
    [step] = replay.correlate(decisions, trades=trades, outcomes=outcomes)
    assert [t["id"] for t in step.trades] == [10]
    assert step.outcome["realized_pnl"] == 12.5


def test_decision_without_decision_id_links_no_fills():
    decisions = [{"id": 1, "strategy": "s", "ts": 100, "action": "HOLD"}]   # no decision_id
    trades = [{"id": 10, "decision_id": "D1", "ts": 100, "action": "OPEN"}]
    outcomes = [{"decision_id": "D1", "realized_pnl": 5}]
    [step] = replay.correlate(decisions, trades=trades, outcomes=outcomes)
    assert step.trades == () and step.outcome is None      # nothing fabricated


def test_risk_veto_joins_on_strategy_and_ts_and_flags_blocked():
    decisions = [{"id": 1, "strategy": "s", "ts": 100, "action": "LONG", "intended_action": "LONG"}]
    risk = [{"id": 1, "strategy": "s", "ts": 100, "kind": "daily_loss", "reason": "cap hit"}]
    [step] = replay.correlate(decisions, risk_events=risk)
    assert step.risk_events[0]["kind"] == "daily_loss"
    assert step.was_blocked


def test_blocked_only_on_explicit_block_or_veto_not_ignored_signal():
    # explicit BLOCKED -> blocked
    s_block = replay.correlate([{"id": 1, "strategy": "s", "ts": 1,
                                 "intended_action": "LONG", "taken_action": "BLOCKED"}])[0]
    assert s_block.was_blocked
    # a LONG signal the engine ignored because a position was already open writes taken=HOLD with
    # NO veto — this is NOT a block, and must not be flagged as one (the audit-tool cardinal sin)
    s_ignored = replay.correlate([{"id": 1, "strategy": "s", "ts": 1,
                                   "intended_action": "LONG", "taken_action": "HOLD"}])[0]
    assert not s_ignored.was_blocked
    s_hold = replay.correlate([{"id": 1, "strategy": "s", "ts": 1,
                                "intended_action": "HOLD", "taken_action": "HOLD"}])[0]
    assert not s_hold.was_blocked


def test_close_fill_links_to_opening_decision_via_position_id():
    decisions = [{"id": 1, "strategy": "s", "ts": 100, "action": "LONG", "decision_id": "D1"}]
    trades = [
        {"id": 10, "decision_id": "D1", "position_id": 7, "ts": 100, "action": "OPEN"},
        {"id": 11, "decision_id": None, "position_id": 7, "ts": 300, "action": "CLOSE"},  # the exit
    ]
    [step] = replay.correlate(decisions, trades=trades)
    assert [t["id"] for t in step.trades] == [10, 11]     # both entry AND exit, chronological


def test_unmatched_kill_switch_halt_surfaces_as_its_own_step():
    decisions = [{"id": 1, "strategy": "ai", "ts": 100, "action": "LONG"}]
    # the kill-switch writes strategy=None at a fresh ts that matches no decision
    halt = [{"id": 1, "strategy": None, "ts": 250, "kind": "halt", "reason": "daily loss cap"}]
    steps = replay.correlate(decisions, risk_events=halt, strategy="ai")
    assert len(steps) == 2                                 # the decision + the standalone halt
    halt_step = steps[1]
    assert halt_step.risk_events[0]["kind"] == "halt" and halt_step.was_blocked


def test_other_strategy_unmatched_veto_is_scoped_out():
    decisions = [{"id": 1, "strategy": "ai", "ts": 100, "action": "LONG"}]
    other = [{"id": 1, "strategy": "rsi_revert", "ts": 250, "kind": "vol_guard", "reason": "x"}]
    steps = replay.correlate(decisions, risk_events=other, strategy="ai")
    assert len(steps) == 1                                 # rsi_revert's veto is not in ai's audit


def test_correlate_is_deterministic():
    decisions = [{"id": i, "strategy": "s", "ts": i * 10, "action": "HOLD", "decision_id": f"D{i}"}
                 for i in range(5)]
    a = replay.render(replay.correlate(decisions))
    b = replay.render(replay.correlate(list(reversed(decisions))))
    assert a == b      # same rows in any input order -> identical replay


# --- rendering -------------------------------------------------------------------------------
def test_render_includes_thesis_fill_outcome_and_flags():
    decisions = [{"id": 1, "strategy": "ai", "ts": 0, "action": "LONG", "taken_action": "LONG",
                  "confidence": 0.8, "reason": "breakout", "regime": "trend", "decision_id": "D1"}]
    llm = [{"id": 1, "strategy": "ai", "ts": 0, "model": "claude", "prediction": "pumps"}]
    trades = [{"id": 1, "decision_id": "D1", "ts": 0, "action": "OPEN", "side": "LONG",
               "size": 1, "price": 100, "slippage": 0.1}]
    outcomes = [{"decision_id": "D1", "realized_pnl": 9.0, "pnl_pct": 9, "exit_reason": "signal",
                 "prediction_correct": 1}]
    text = replay.render(replay.correlate(decisions, llm, trades, outcomes))
    assert "ai" in text and "breakout" in text and "pumps" in text
    assert "fill:" in text and "pnl=9.0" in text


def test_render_empty_is_empty_string():
    assert replay.render([]) == ""


# --- DB round-trip through the repository ----------------------------------------------------
@pytest.fixture
def repo(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    yield Repository(db)
    db.close()


def test_build_replay_end_to_end(repo):
    # two ticks: a clean LONG that fills, and a later HOLD
    repo.log_decision("ai", 1000, 1000, "LONG", 0.7, "breakout", "{}",
                      decision_id="D1", intended_action="LONG", taken_action="LONG", regime="trend")
    repo.record_llm_response("ai", 1000, "api", "claude", "LONG", 0.7,
                             "up channel", "higher", "momentum", "{}", None)
    repo.log_decision("ai", 2000, 2000, "HOLD", 0.5, "chop", "{}",
                      decision_id=None, intended_action="HOLD", taken_action="HOLD", regime="range")
    steps = replay.build_replay(repo, strategy="ai")
    assert [s.ts for s in steps] == [1000, 2000]            # chronological
    assert steps[0].llm["prediction"] == "higher"          # thesis recovered by exact ts
    assert steps[1].llm is None                             # the HOLD tick had no consult
    text = replay.render(steps, verbose=True)
    assert "decision_id=D1" in text


def test_build_replay_window_filters(repo):
    repo.log_decision("ai", 1000, 1000, "LONG", 0.7, "a", "{}")
    repo.log_decision("ai", 5000, 5000, "SHORT", 0.7, "b", "{}")
    # window excludes the second decision
    steps = replay.build_replay(repo, strategy="ai",
                                since_iso="1970-01-01T00:00:01Z", until_iso="1970-01-01T00:00:02Z")
    assert [s.ts for s in steps] == [1000]
