from homing_trade.db import Database
from homing_trade.broker import Broker
from homing_trade.engine import process_tick
from homing_trade.config import Config
from homing_trade.models import Candle, Signal


def candles(n=30):
    return [Candle(open=100, high=101, low=99, close=100, volume=1, time=1000 + i * 60000)
            for i in range(n)]


class StubAI:
    backend = "cli"
    model = "claude-opus-4-8"

    def __init__(self, signal, name="llm_claude_code"):
        self.name = name
        self._sig = signal

    def on_candle(self, candles, position):
        return self._sig


class _Notifier:
    def __init__(self):
        self.alerts = []

    def notify(self, level, title, msg):
        self.alerts.append((level, title, msg))


def test_db_llm_response_roundtrip(tmp_path):
    db = Database(str(tmp_path / "z.db"))
    db.record_llm_response("llm_anthropic", 1000, "api", "claude-opus-4-8", "LONG", 0.7,
                           "obs", "pred", "why", "{raw}", None)
    rows = db.recent_llm_responses()
    assert len(rows) == 1 and rows[0]["action"] == "LONG"
    assert db.latest_llm_rationale("llm_anthropic") == "why"
    db.close()


def test_process_tick_persists_reasoning(tmp_path):
    db = Database(str(tmp_path / "x.db"))
    db.ensure_strategy("llm_claude_code", 5000.0)
    sig = Signal("HOLD", confidence=0.4, reason="r", raw='{"env":1}',
                 meta={"observation": "saw chop", "prediction": "sideways", "rationale": "no edge"})
    process_tick(db, Broker(0.0005, 0.0005), [StubAI(sig)], candles(), Config())
    rows = db.recent_llm_responses("llm_claude_code")
    assert len(rows) == 1
    assert rows[0]["observation"] == "saw chop"
    assert rows[0]["prediction"] == "sideways"
    assert rows[0]["rationale"] == "no edge"
    assert rows[0]["raw"] == '{"env":1}'
    db.close()


def test_error_alerts_discord_and_dedups(tmp_path):
    db = Database(str(tmp_path / "e.db"))
    db.ensure_strategy("llm_claude_code", 5000.0)
    n = _Notifier()
    skill = StubAI(Signal("HOLD", reason="llm unavailable: boom", error="boom"))
    process_tick(db, Broker(0.0005, 0.0005), [skill], candles(), Config(), None, n)
    assert any(level == "error" for level, _, _ in n.alerts)
    # same error again -> deduped (no second alert)
    before = len(n.alerts)
    process_tick(db, Broker(0.0005, 0.0005), [skill], candles(), Config(), None, n)
    assert len(n.alerts) == before
    # the error is persisted both times for the audit trail
    assert len(db.recent_llm_responses("llm_claude_code")) == 2
    db.close()
