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


def test_db_llm_response_stores_replay_fields(tmp_path):
    import json as _json
    db = Database(str(tmp_path / "r.db"))
    db.record_llm_response("llm_anthropic", 1000, "api", "m", "HOLD", 0.3,
                           "o", "p", "w", "{raw}", None,
                           next_check_in_sec=120,
                           requested_charts=[{"interval": "1h", "limit": 300}])
    row = db.recent_llm_responses()[0]
    assert row["next_check_in_sec"] == 120
    assert _json.loads(row["requested_charts"]) == [{"interval": "1h", "limit": 300}]
    db.close()


def test_process_tick_persists_replay_fields(tmp_path):
    import json as _json
    db = Database(str(tmp_path / "rt.db"))
    db.ensure_strategy("llm_claude_code", 5000.0)
    sig = Signal("HOLD", confidence=0.4, reason="r", raw='{"env":1}',
                 meta={"observation": "o", "prediction": "p", "rationale": "w",
                       "next_check_in_sec": 90,
                       "requested_charts": [{"interval": "5m", "limit": 150}]})
    process_tick(db, Broker(0.0005, 0.0005), [StubAI(sig)], candles(), Config())
    row = db.recent_llm_responses("llm_claude_code")[0]
    assert row["next_check_in_sec"] == 90
    assert _json.loads(row["requested_charts"]) == [{"interval": "5m", "limit": 150}]
    db.close()


def test_process_tick_persists_prompt_and_playbook_version(tmp_path):
    # The playbook-injection versioning the AI trader emits must land on BOTH the llm_responses
    # row (prompt_version + prompt_hash) and the decision_log row (prompt_version +
    # playbook_version) so a decision is fully replayable and attributable to a playbook.
    db = Database(str(tmp_path / "pv.db"))
    db.ensure_strategy("llm_claude_code", 5000.0)
    sig = Signal("HOLD", confidence=0.4, reason="r", raw='{"env":1}',
                 meta={"observation": "o", "prediction": "p", "rationale": "w",
                       "prompt_version": "mtf-v1+ma-v2", "playbook_version": "ma-v2",
                       "prompt_hash": "deadbeefcafef00d"})
    process_tick(db, Broker(0.0005, 0.0005), [StubAI(sig)], candles(), Config())
    lr = db.recent_llm_responses("llm_claude_code")[0]
    assert lr["prompt_version"] == "mtf-v1+ma-v2" and lr["prompt_hash"] == "deadbeefcafef00d"
    d = db.conn.execute("SELECT prompt_version, playbook_version FROM decision_log "
                        "WHERE strategy='llm_claude_code'").fetchone()
    assert d["prompt_version"] == "mtf-v1+ma-v2" and d["playbook_version"] == "ma-v2"
    db.close()


def test_skillrunner_wires_playbook_provider_from_ledger(tmp_path):
    # End-to-end wiring: SkillRunner gives each AI trader a provider that reads the CURRENT
    # published playbook for that trader's own strategy out of the ledger.
    from homing_trade.engine import SkillRunner
    from homing_trade.repository import Repository
    repo = Repository.open(str(tmp_path / "sr.db"))
    repo.publish_playbook("cc-v1", "llm_claude_code", 1000, {"rules": ["trend only", "skip chop"]})
    cfg = Config()
    cfg.ai_claude_code_enabled = True          # spin up one AI trader
    cfg.enabled_skills = []                     # no mechanical skills needed for this check
    runner = SkillRunner(cfg, repo, Broker(0.0005, 0.0005))
    ai = next(t for t in runner.ai_traders if t.name == "llm_claude_code")
    version, rules = ai._current_playbook()
    assert version == "cc-v1" and rules == ["trend only", "skip chop"]
    repo.close()


def test_skillrunner_wiring_is_per_trader_not_late_bound(tmp_path):
    # Regression guard for the closure: with TWO AI traders, each must read ITS OWN playbook.
    # A naive `lambda: latest_playbook(t.name)` would late-bind and make both read the last one.
    from homing_trade.engine import SkillRunner
    from homing_trade.repository import Repository
    repo = Repository.open(str(tmp_path / "sr2.db"))
    repo.publish_playbook("cc-v1", "llm_claude_code", 1000, {"rules": ["cc rule"]})
    repo.publish_playbook("an-v1", "llm_anthropic", 1000, {"rules": ["an rule"]})
    cfg = Config()
    cfg.ai_claude_code_enabled = True
    cfg.ai_anthropic_enabled = True
    cfg.enabled_skills = []
    runner = SkillRunner(cfg, repo, Broker(0.0005, 0.0005))
    by_name = {t.name: t._current_playbook() for t in runner.ai_traders}
    assert by_name["llm_claude_code"] == ("cc-v1", ["cc rule"])
    assert by_name["llm_anthropic"] == ("an-v1", ["an rule"])
    repo.close()


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
