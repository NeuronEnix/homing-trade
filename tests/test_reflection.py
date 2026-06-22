"""The primary learn->correct loop: batching, embargo, mechanical scoring, human-gated output."""
import json
from homing_trade.repository import Repository
from homing_trade.reflection import ReflectionEngine


def seed_outcomes(repo, strategy="ma", n=6, base_ts=1000, step=1000):
    """n completed round-trips (OPEN+CLOSE), alternating win/loss, then rebuild outcomes."""
    repo.ensure_strategy(strategy, 5000.0)
    for i in range(n):
        pid = i + 1
        ot = base_ts + i * step
        ct = ot + step // 2
        win = i % 2 == 0
        exit_px = 110.0 if win else 95.0
        pnl = 9.9 if win else -5.1
        repo.record_trade(strategy, pid, "LONG", "OPEN", 100.0, 1, 0.1, -0.1, ot,
                          regime_at_entry="trend_up")
        repo.record_trade(strategy, pid, "LONG", "CLOSE", exit_px, 1, 0.1, pnl, ct,
                          exit_reason="signal")
    repo.rebuild_trade_outcomes()


def fixed_llm(payload):
    """A deterministic 'model' that returns a lesson + a rule, wrapped in prose+markdown."""
    def _fn(prompt):
        return "Here is my review.\n```json\n" + json.dumps(payload) + "\n```\nThanks."
    return _fn


def test_run_once_writes_reflection_and_proposal(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    seed_outcomes(repo, n=6)
    eng = ReflectionEngine(repo, fixed_llm({"lesson": "stops too tight in chop",
                                            "rules": ["skip chop", "trend only"]}),
                           min_trades=3)
    out = eng.run_once("ma", now_ms=100000)
    assert out is not None and out["n_trades"] == 6
    # reflection persisted with the batch + lesson
    refl = repo.recent_reflections("ma")[0]
    assert refl["lesson"] == "stops too tight in chop" and refl["kind"] == "periodic"
    assert refl["batch_to_ts"] == 100000 and json.loads(refl["trade_ids_json"])
    # a human-gated playbook proposal was filed, linked back to the reflection
    props = repo.pending_proposals("ma")
    assert len(props) == 1 and props[0]["kind"] == "playbook"
    payload = json.loads(props[0]["payload_json"])
    assert payload["rules"] == ["skip chop", "trend only"] and payload["version"] == out["new_version"]
    assert props[0]["source_reflection_id"] == out["reflection_id"]
    assert props[0]["status"] == "pending"             # nothing applied; awaits approval
    assert repo.latest_playbook("ma") is None          # NOT published by reflection
    repo.close()


def test_min_trades_gate(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    seed_outcomes(repo, n=2)
    eng = ReflectionEngine(repo, fixed_llm({"lesson": "x", "rules": ["y"]}), min_trades=5)
    assert eng.run_once("ma", now_ms=100000) is None    # too few new outcomes
    assert repo.recent_reflections("ma") == [] and repo.pending_proposals() == []
    repo.close()


def test_watermark_skips_already_reflected_trades(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    seed_outcomes(repo, n=6)
    eng = ReflectionEngine(repo, fixed_llm({"lesson": "a", "rules": ["b"]}), min_trades=3)
    assert eng.run_once("ma", now_ms=100000) is not None
    # second pass: no NEW outcomes since the last reflection's batch_to_ts -> skip
    assert eng.run_once("ma", now_ms=200000) is None
    assert len(repo.recent_reflections("ma")) == 1
    repo.close()


def test_embargo_hides_unrealized_outcomes(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    seed_outcomes(repo, n=6, base_ts=10000, step=2000)   # closes land well after now_ms below
    eng = ReflectionEngine(repo, fixed_llm({"lesson": "x", "rules": ["y"]}), min_trades=1)
    # now_ms before any close realizes -> embargo hides them all -> nothing to reflect on
    assert eng.run_once("ma", now_ms=5000) is None
    repo.close()


def test_no_model_is_noop(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    seed_outcomes(repo, n=6)
    assert ReflectionEngine(repo, None, min_trades=1).run_once("ma", now_ms=100000) is None
    repo.close()


def test_model_error_never_crashes(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    seed_outcomes(repo, n=6)
    def boom(_prompt):
        raise RuntimeError("model down")
    eng = ReflectionEngine(repo, boom, min_trades=1)
    assert eng.run_once("ma", now_ms=100000) is None     # swallowed
    assert repo.recent_reflections("ma") == [] and repo.pending_proposals() == []
    repo.close()


def test_unparseable_response_is_skipped(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    seed_outcomes(repo, n=6)
    eng = ReflectionEngine(repo, lambda p: "no json here", min_trades=1)
    assert eng.run_once("ma", now_ms=100000) is None
    assert repo.recent_reflections("ma") == []
    repo.close()


def test_empty_rules_records_reflection_but_no_proposal(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    seed_outcomes(repo, n=6)
    eng = ReflectionEngine(repo, fixed_llm({"lesson": "no change warranted", "rules": []}),
                           min_trades=1)
    out = eng.run_once("ma", now_ms=100000)
    assert out is not None and out["proposal_id"] is None and out["new_version"] is None
    assert repo.recent_reflections("ma")[0]["lesson"] == "no change warranted"
    assert repo.pending_proposals() == []                # a no-op lesson proposes nothing
    repo.close()


def test_valid_json_non_dict_response_is_skipped(tmp_path):
    # The model returns valid JSON that isn't an object (a bare list of rules). Must not crash
    # the loop on .get(); must write nothing.
    repo = Repository.open(str(tmp_path / "r.db"))
    seed_outcomes(repo, n=6)
    eng = ReflectionEngine(repo, lambda p: '["skip chop", "trend only"]', min_trades=1)
    assert eng.run_once("ma", now_ms=100000) is None
    assert repo.recent_reflections("ma") == [] and repo.pending_proposals() == []
    repo.close()


def test_protected_key_in_rule_is_dropped_not_crashed(tmp_path):
    # A rule that is an OBJECT carrying a protected field must not reach create_proposal (which
    # would raise on the protected key and orphan the reflection / crash the loop). Non-string
    # rules are dropped; legit string rules survive.
    repo = Repository.open(str(tmp_path / "r.db"))
    seed_outcomes(repo, n=6)
    eng = ReflectionEngine(repo, fixed_llm({"lesson": "tighten in chop",
                                            "rules": ["skip chop", {"leverage": 3}]}),
                           min_trades=1)
    out = eng.run_once("ma", now_ms=100000)
    assert out is not None                                  # no crash
    props = repo.pending_proposals("ma")
    assert len(props) == 1
    assert json.loads(props[0]["payload_json"])["rules"] == ["skip chop"]  # object rule dropped
    repo.close()


def test_prompt_presents_mechanical_metrics_and_does_not_self_grade(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    seed_outcomes(repo, n=6)
    captured = {}
    def capture(prompt):
        captured["p"] = prompt
        return json.dumps({"lesson": "x", "rules": []})
    ReflectionEngine(repo, capture, min_trades=1).run_once("ma", now_ms=100000)
    p = captured["p"]
    assert "directional_accuracy" in p and "by_regime" in p
    assert "do NOT re-grade" in p          # the model is told the scoring is mechanical
    repo.close()


# --- the secondary loop: per-closed-trade Reflexion -----------------------------------------

def seed_one_trade(repo, strategy="ma", pid=1, did="d1", ot=1000, ct=1500, entry=100.0,
                   exit_px=92.0, side="LONG", *, with_thesis=True,
                   obs="range-bound at support", pred="break up toward 110",
                   why="ema cross up + rsi turning"):
    """One completed round-trip wired to its originating decision + (optionally) AI thesis,
    linked by decision_id, then the outcome table rebuilt."""
    repo.ensure_strategy(strategy, 5000.0)
    if with_thesis:
        repo.log_decision(strategy, ot, ot, side, 0.8, "ema cross", {"ema": 1},
                          decision_id=did, intended_action=side, taken_action=side,
                          regime="trend_up")
        repo.record_llm_response(strategy, ot, "cli", "claude", side, 0.8,
                                 obs, pred, why, "{...}", "")
    repo.record_trade(strategy, pid, side, "OPEN", entry, 1, 0.1, -0.1, ot,
                      decision_id=(did if with_thesis else None), regime_at_entry="trend_up")
    repo.record_trade(strategy, pid, side, "CLOSE", exit_px, 1, 0.1, exit_px - entry, ct,
                      exit_reason="stop")
    repo.rebuild_trade_outcomes()


def test_reflect_on_trade_writes_per_trade_reflection(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    seed_one_trade(repo)                                   # LONG 100->92: predicted up, fell
    eng = ReflectionEngine(repo, fixed_llm({"lesson": "ignored chop; entered against trend"}))
    out = eng.reflect_on_trade("ma", 1, now_ms=100000)
    assert out is not None and out["position_id"] == 1
    assert out["prediction_correct"] == 0                  # LONG that fell -> mechanically wrong
    refl = repo.recent_reflections("ma", kind="per_trade")[0]
    assert refl["kind"] == "per_trade"
    assert refl["lesson"] == "ignored chop; entered against trend"
    assert json.loads(refl["trade_ids_json"]) == [1]
    assert refl["new_playbook_version"] is None            # per-trade proposes no playbook
    m = json.loads(refl["metrics_json"])
    assert m["prediction_correct"] == 0 and m["exit_reason"] == "stop"
    assert m["realized_pnl"] == -8.1                       # OPEN -0.1 + CLOSE (92-100)=-8.0
    assert repo.pending_proposals() == []                  # the SECONDARY loop files no proposal
    repo.close()


def test_reflect_on_trade_prompt_has_thesis_and_mechanical_truth(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    seed_one_trade(repo)
    cap = {}
    ReflectionEngine(repo, lambda p: (cap.__setitem__("p", p),
                                      json.dumps({"lesson": "x"}))[1]).reflect_on_trade(
        "ma", 1, now_ms=100000)
    p = cap["p"]
    # the model sees what it SAID (thesis) ...
    assert "break up toward 110" in p and "range-bound at support" in p
    # ... and the MECHANICAL ground truth, told not to re-grade it
    assert "prediction_correct" in p and "exit_reason" in p
    assert "do NOT re-grade" in p
    repo.close()


def test_reflect_on_trade_embargo_hides_unrealized(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    seed_one_trade(repo, ct=50000)                         # closes at ts=50000
    eng = ReflectionEngine(repo, fixed_llm({"lesson": "y"}))
    assert eng.reflect_on_trade("ma", 1, now_ms=10000) is None   # before realize -> embargoed
    assert repo.recent_reflections("ma", kind="per_trade") == []
    repo.close()


def test_reflect_on_trade_missing_trade_is_noop(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    seed_one_trade(repo)
    eng = ReflectionEngine(repo, fixed_llm({"lesson": "y"}))
    assert eng.reflect_on_trade("ma", 999, now_ms=100000) is None   # no such position
    repo.close()


def test_reflect_on_trade_no_model_or_error_is_noop(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    seed_one_trade(repo)
    assert ReflectionEngine(repo, None).reflect_on_trade("ma", 1, now_ms=100000) is None
    def boom(_p):
        raise RuntimeError("down")
    assert ReflectionEngine(repo, boom).reflect_on_trade("ma", 1, now_ms=100000) is None
    assert repo.recent_reflections("ma", kind="per_trade") == []
    repo.close()


def test_reflect_on_trade_empty_lesson_records_nothing(tmp_path):
    repo = Repository.open(str(tmp_path / "r.db"))
    seed_one_trade(repo)
    eng = ReflectionEngine(repo, fixed_llm({"lesson": "   "}))   # nothing concrete to learn
    assert eng.reflect_on_trade("ma", 1, now_ms=100000) is None
    assert repo.recent_reflections("ma", kind="per_trade") == []
    repo.close()


def test_reflect_on_trade_works_for_mechanical_trade_without_thesis(tmp_path):
    # A mechanical-skill trade has a decision but no AI observation/prediction/rationale.
    # The critique still runs (degrading the missing thesis fields to empty), never crashes.
    repo = Repository.open(str(tmp_path / "r.db"))
    seed_one_trade(repo, with_thesis=False)
    out = ReflectionEngine(repo, fixed_llm({"lesson": "stop too tight"})).reflect_on_trade(
        "ma", 1, now_ms=100000)
    assert out is not None
    assert repo.recent_reflections("ma", kind="per_trade")[0]["lesson"] == "stop too tight"
    repo.close()


def test_reflect_on_trade_is_idempotent_per_position(tmp_path):
    # ONE critique per trade: a second call for the same position re-spends nothing on the model
    # and writes no second reflection (safe regardless of how the close-hook caller is wired).
    repo = Repository.open(str(tmp_path / "r.db"))
    seed_one_trade(repo)
    calls = {"n": 0}
    def once(_p):
        calls["n"] += 1
        return json.dumps({"lesson": "stop too tight"})
    eng = ReflectionEngine(repo, once)
    assert eng.reflect_on_trade("ma", 1, now_ms=100000) is not None
    assert eng.reflect_on_trade("ma", 1, now_ms=120000) is None    # already reflected -> no-op
    assert calls["n"] == 1                                          # model NOT re-consulted
    assert len(repo.recent_reflections("ma", kind="per_trade")) == 1
    repo.close()


def test_reflect_on_trade_non_string_lesson_is_dropped_not_crashed(tmp_path):
    # A model that returns a non-string lesson (a list) must not reach .strip() and crash the
    # loop; it's treated as "nothing learned" -> no reflection.
    repo = Repository.open(str(tmp_path / "r.db"))
    seed_one_trade(repo)
    eng = ReflectionEngine(repo, fixed_llm({"lesson": ["a", "b"]}))
    assert eng.reflect_on_trade("ma", 1, now_ms=100000) is None
    assert repo.recent_reflections("ma", kind="per_trade") == []
    repo.close()


def test_per_trade_reflection_does_not_corrupt_periodic_watermark(tmp_path):
    # CROSS-LOOP ISOLATION: the periodic loop's watermark is the last PERIODIC reflection's
    # batch_to_ts. A per_trade reflection (more recent by ts, but covering one old trade) must
    # NOT be mistaken for that watermark, or the periodic loop would re-reflect old outcomes.
    repo = Repository.open(str(tmp_path / "r.db"))
    seed_outcomes(repo, n=6)                               # outcomes realized by ts~6500
    eng = ReflectionEngine(repo, fixed_llm({"lesson": "a", "rules": ["b"]}), min_trades=3)
    assert eng.run_once("ma", now_ms=100000) is not None   # periodic reflection, watermark=100000
    # a per_trade reflection lands LATER in ts but covers an OLD trade (batch_to_ts ~1500)
    eng.reflect_on_trade("ma", 1, now_ms=150000)
    assert repo.recent_reflections("ma", kind="per_trade")                       # it was recorded
    # periodic pass again: no NEW outcomes since the periodic watermark (100000) -> must skip,
    # NOT be fooled into re-reflecting by the more-recent per_trade row.
    assert eng.run_once("ma", now_ms=200000) is None
    assert len(repo.recent_reflections("ma", kind="periodic")) == 1
    repo.close()
