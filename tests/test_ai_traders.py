from homing_trade.ai_traders import build_ai_traders
from homing_trade.config import Config


def test_none_enabled_by_default():
    assert build_ai_traders(Config()) == []


def test_claude_code_only():
    ts = build_ai_traders(Config(ai_claude_code_enabled=True, ai_claude_code_poll_sec=30))
    assert len(ts) == 1
    assert ts[0].name == "llm_claude_code"
    assert ts[0].backend == "cli"
    assert ts[0].interval_sec == 30


def test_anthropic_only():
    ts = build_ai_traders(Config(ai_anthropic_enabled=True, ai_anthropic_poll_sec=20))
    assert len(ts) == 1
    assert ts[0].name == "llm_anthropic"
    assert ts[0].backend == "api"
    assert ts[0].interval_sec == 20


def test_both_run_independently():
    ts = build_ai_traders(Config(ai_claude_code_enabled=True, ai_anthropic_enabled=True))
    assert len(ts) == 2
    assert {t.name for t in ts} == {"llm_claude_code", "llm_anthropic"}
    assert {t.backend for t in ts} == {"cli", "api"}
    # distinct names => distinct wallets => they trade independently
    assert ts[0].name != ts[1].name
