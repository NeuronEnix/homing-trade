"""Cadence wiring for the periodic reflection loop: ReflectionRunner fires run_once on a slow
wall-clock cadence, gated default-OFF, never crashing the engine loop."""
import json
from homing_trade.repository import Repository
from homing_trade.reflect_runner import ReflectionRunner, build_reflect_fn
from homing_trade.config import Config
from homing_trade.models import Candle


def seed(repo, strategy="ma", n=6, base=1000, step=1000, pid0=1):
    # position_id is globally unique in production (positions.id AUTOINCREMENT) and
    # rebuild_trade_outcomes groups by it across strategies — so each strategy needs its own
    # pid range, else their round-trips merge into one outcome group.
    repo.ensure_strategy(strategy, 5000.0)
    for i in range(n):
        pid, ot = pid0 + i, base + i * step
        ct = ot + step // 2
        win = i % 2 == 0
        repo.record_trade(strategy, pid, "LONG", "OPEN", 100.0, 1, 0.1, -0.1, ot,
                          regime_at_entry="trend_up")
        repo.record_trade(strategy, pid, "LONG", "CLOSE", 110.0 if win else 95.0, 1, 0.1,
                          9.9 if win else -5.1, ct, exit_reason="signal")
    repo.rebuild_trade_outcomes()


def fixed(payload):
    return lambda _p: "```json\n" + json.dumps(payload) + "\n```"


def test_runner_reflects_due_strategy_and_files_proposal(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    seed(repo)
    r = ReflectionRunner(repo, fixed({"lesson": "stops too tight", "rules": ["skip chop"]}),
                         poll_sec=600, min_trades=3, clock=lambda: 1000.0)
    out = r.run(["ma"])
    assert len(out) == 1 and out[0]["n_trades"] == 6
    assert repo.recent_reflections("ma")[0]["lesson"] == "stops too tight"
    assert len(repo.pending_proposals("ma")) == 1            # human-gated playbook proposal filed
    repo.close()


def test_runner_cadence_gates_llm_calls(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    seed(repo)
    calls = {"n": 0}
    def fn(_p):
        calls["n"] += 1
        return json.dumps({"lesson": "x", "rules": ["a"]})
    clock = [1000.0]
    r = ReflectionRunner(repo, fn, poll_sec=600, min_trades=3, clock=lambda: clock[0])
    r.run(["ma"]); assert calls["n"] == 1                    # first pass consults
    clock[0] += 300; r.run(["ma"]); assert calls["n"] == 1   # within poll_sec -> NOT consulted
    repo.close()


def test_runner_also_files_confidence_floor_proposal(tmp_path):
    # The reflection cadence also runs the mechanical calibration -> a confidence-floor proposal.
    # Use an empty-rules reflect_fn so the ONLY proposal filed is the calibration one.
    repo = Repository.open(str(tmp_path / "r.db"))
    repo.ensure_strategy("ma", 5000.0)
    pid = 1
    for _ in range(6):   # low-confidence losers
        repo.log_decision("ma", 1000 + pid, 1000 + pid, "LONG", 0.30, "r", {}, decision_id=f"d{pid}",
                          intended_action="LONG", taken_action="LONG")
        repo.record_trade("ma", pid, "LONG", "OPEN", 100.0, 1, 0.1, -0.1, 1000 + pid, decision_id=f"d{pid}")
        repo.record_trade("ma", pid, "LONG", "CLOSE", 95.0, 1, 0.1, -5.0, 1100 + pid, exit_reason="signal")
        pid += 1
    for _ in range(6):   # high-confidence winners
        repo.log_decision("ma", 1000 + pid, 1000 + pid, "LONG", 0.70, "r", {}, decision_id=f"d{pid}",
                          intended_action="LONG", taken_action="LONG")
        repo.record_trade("ma", pid, "LONG", "OPEN", 100.0, 1, 0.1, -0.1, 1000 + pid, decision_id=f"d{pid}")
        repo.record_trade("ma", pid, "LONG", "CLOSE", 110.0, 1, 0.1, 10.0, 1100 + pid, exit_reason="signal")
        pid += 1
    repo.rebuild_trade_outcomes()
    r = ReflectionRunner(repo, fixed({"lesson": "x", "rules": []}), poll_sec=600, min_trades=3,
                         clock=lambda: 1_000_000.0)
    r.run(["ma"])
    floors = [p for p in repo.pending_proposals("ma") if p["kind"] == "param"]
    assert len(floors) == 1
    assert json.loads(floors[0]["payload_json"])["confidence_floor"] == 0.6
    repo.close()


def test_runner_disabled_when_no_reflect_fn(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    seed(repo)
    r = ReflectionRunner(repo, None, min_trades=1)
    assert r.enabled is False
    assert r.run(["ma"]) == []                               # no-op
    assert repo.recent_reflections("ma") == [] and repo.pending_proposals() == []
    repo.close()


def test_runner_never_crashes_on_model_error(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    seed(repo)
    def boom(_p):
        raise RuntimeError("model down")
    r = ReflectionRunner(repo, boom, min_trades=1, clock=lambda: 1000.0)
    assert r.run(["ma"]) == []                               # swallowed, loop survives
    repo.close()


def test_runner_independent_cadence_per_strategy(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    seed(repo, "ma", pid0=1); seed(repo, "rsi", pid0=101)
    seen = []
    def fn(_p):
        return json.dumps({"lesson": "x", "rules": []})
    clock = [1000.0]
    r = ReflectionRunner(repo, fn, poll_sec=600, min_trades=3, clock=lambda: clock[0])
    r.run(["ma"])                                            # only ma is due/run this pass
    clock[0] += 100
    r.run(["rsi"])                                           # rsi runs on its own first-seen pass
    assert {x["strategy"] for x in repo.recent_reflections()} == {"ma", "rsi"}
    repo.close()


def test_build_reflect_fn_none_when_disabled():
    assert build_reflect_fn(Config()) is None                # reflection_enabled defaults False


def test_build_reflect_fn_callable_when_enabled():
    cfg = Config(); cfg.reflection_enabled = True
    fn = build_reflect_fn(cfg)
    assert callable(fn)                                      # production factory wired (CLI/API)


def test_skillrunner_wires_disabled_reflection_by_default(tmp_path):
    from homing_trade.engine import SkillRunner
    from homing_trade.broker import Broker
    cfg = Config(db_path=str(tmp_path / "sr.db"), enabled_skills=["ma_trend"],
                 ai_claude_code_enabled=False, ai_anthropic_enabled=False)
    repo = Repository.open(cfg.db_path)
    runner = SkillRunner(cfg, repo, Broker(cfg.fee, cfg.slippage))
    assert runner.reflection.enabled is False               # OFF unless reflection_enabled
    # run_tick must not crash with reflection wired in
    cs = [Candle(open=100, high=101, low=99, close=100, volume=1, time=1000 + i * 60000)
          for i in range(40)]
    runner.run_tick(cs, is_paused=lambda: False, commands=None)
    repo.close()


def test_run_tick_calls_reflection_with_ai_names_only(tmp_path):
    # run_tick must drive the reflection loop, scoped to the AI traders (the playbook consumers).
    from homing_trade.engine import SkillRunner
    from homing_trade.broker import Broker
    cfg = Config(db_path=str(tmp_path / "sr.db"), enabled_skills=["ma_trend"],
                 ai_claude_code_enabled=False, ai_anthropic_enabled=False)
    repo = Repository.open(cfg.db_path)
    runner = SkillRunner(cfg, repo, Broker(cfg.fee, cfg.slippage))
    runner._ai_names = {"llm_b", "llm_a"}                    # pretend two AI traders exist

    class _Spy:
        def __init__(self): self.calls = []
        def run(self, strategies): self.calls.append(list(strategies))
    spy = _Spy()
    runner.reflection = spy
    cs = [Candle(open=100, high=101, low=99, close=100, volume=1, time=1000 + i * 60000)
          for i in range(40)]
    runner.run_tick(cs, is_paused=lambda: False, commands=None)
    assert spy.calls == [["llm_a", "llm_b"]]                 # sorted AI names, mech excluded
    repo.close()
