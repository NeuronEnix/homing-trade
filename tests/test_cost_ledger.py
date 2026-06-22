"""Phase 5 #4: per-provider cost accounting.

A cost_ledger row is written per AI consult that reported usage (machine-written audit-truth),
attributed to strategy/model/backend. SelfQuery.cost_summary rolls it up per provider; build_state
surfaces tokens + $ on the leaderboard. NULL tokens/usd are honest (a provider may not report them).
"""
import pytest
from homing_trade.db import Database, AUDIT_TRUTH_TABLES
from homing_trade.broker import Broker
from homing_trade.engine import process_tick
from homing_trade.config import Config
from homing_trade.repository import Repository
from homing_trade.selfquery import SelfQuery
from homing_trade.models import Candle, Signal


def candles(n=30):
    return [Candle(open=100, high=101, low=99, close=100, volume=1, time=1000 + i * 60000)
            for i in range(n)]


class StubAI:
    backend = "api"
    model = "claude-opus-4-8"

    def __init__(self, signal, name="llm_anthropic"):
        self.name = name
        self._sig = signal


    def on_candle(self, candles, position):
        return self._sig


def test_cost_ledger_is_audit_truth():
    assert "cost_ledger" in AUDIT_TRUTH_TABLES


def test_record_cost_and_summary(tmp_path):
    db = Database(str(tmp_path / "c.db"))
    db.record_cost("llm_anthropic", 1000, "claude-opus-4-8", "api", 100, 20, 0.003)
    db.record_cost("llm_anthropic", 2000, "claude-opus-4-8", "api", 50, 10, 0.0015)
    db.record_cost("llm_grok", 1500, "grok-2", "openai", 200, 40, None)   # usd unknown
    s = db.cost_summary()
    a = s["llm_anthropic"]
    assert (a["calls"], a["prompt_tokens"], a["completion_tokens"], a["total_tokens"]) == (2, 150, 30, 180)
    assert a["usd"] == pytest.approx(0.0045)
    assert s["llm_grok"]["calls"] == 1 and s["llm_grok"]["usd"] == 0.0     # NULL usd sums as 0
    assert db.cost_summary("llm_grok").keys() == {"llm_grok"}             # filterable
    db.close()


def test_selfquery_cost_summary_reads(tmp_path):
    repo = Repository.open(str(tmp_path / "sq.db"))
    repo.record_cost("llm_anthropic", 1000, "m", "api", 10, 5, 0.001)
    assert SelfQuery(repo).cost_summary()["llm_anthropic"]["total_tokens"] == 15
    repo.close()


def test_process_tick_records_cost_on_consult_with_usage(tmp_path):
    db = Database(str(tmp_path / "pt.db"))
    db.ensure_strategy("llm_anthropic", 5000.0)
    sig = Signal("HOLD", confidence=0.4, reason="r", raw='{"env":1}',
                 meta={"observation": "o", "prediction": "p", "rationale": "w",
                       "usage": {"prompt_tokens": 120, "completion_tokens": 30, "usd": 0.0045}})
    process_tick(db, Broker(0.0005, 0.0005), [StubAI(sig)], candles(), Config())
    s = db.cost_summary()
    assert s["llm_anthropic"] == {"calls": 1, "prompt_tokens": 120, "completion_tokens": 30,
                                  "total_tokens": 150, "usd": 0.0045}
    row = db.conn.execute("SELECT model, backend FROM cost_ledger").fetchone()
    assert row["model"] == "claude-opus-4-8" and row["backend"] == "api"
    db.close()


def test_process_tick_skips_cost_on_error(tmp_path):
    # An errored consult (HOLD, no usage) must not write a cost row.
    db = Database(str(tmp_path / "pe.db"))
    db.ensure_strategy("llm_anthropic", 5000.0)
    sig = Signal("HOLD", reason="llm unavailable: boom", error="boom")
    process_tick(db, Broker(0.0005, 0.0005), [StubAI(sig)], candles(), Config())
    assert db.cost_summary() == {}
    db.close()


def test_process_tick_skips_cost_when_no_usage(tmp_path):
    # A waiting HOLD (no raw, no usage) writes no cost row either.
    db = Database(str(tmp_path / "pw.db"))
    db.ensure_strategy("llm_anthropic", 5000.0)
    sig = Signal("HOLD", reason="waiting", raw='{"env":1}',
                 meta={"observation": "o", "prediction": "p", "rationale": "w"})  # no usage key
    process_tick(db, Broker(0.0005, 0.0005), [StubAI(sig)], candles(), Config())
    assert db.cost_summary() == {}
    db.close()


def test_process_tick_records_cost_with_only_tokens_no_usd(tmp_path):
    # usd None (unknown model) but tokens present -> still recorded.
    db = Database(str(tmp_path / "pn.db"))
    db.ensure_strategy("llm_grok", 5000.0)
    sig = Signal("HOLD", confidence=0.4, reason="r", raw='{"env":1}',
                 meta={"observation": "o", "prediction": "p", "rationale": "w",
                       "usage": {"prompt_tokens": 80, "completion_tokens": 12, "usd": None}})
    process_tick(db, Broker(0.0005, 0.0005), [StubAI(sig, name="llm_grok")], candles(), Config())
    s = db.cost_summary()
    assert s["llm_grok"]["total_tokens"] == 92 and s["llm_grok"]["usd"] == 0.0
    db.close()
