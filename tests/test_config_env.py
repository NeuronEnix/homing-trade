from homing_trade.config import Config, from_env, effective_leverage

MISSING = "/no/such/.env"  # isolate tests from the real repo .env


def test_futures_and_leverage_defaults():
    c = Config()
    assert c.pair_candles == "B-BTC_USDT"   # futures perpetual, not INR spot
    assert c.leverage_min == 1.0 and c.leverage_max == 15.0
    assert c.max_trade_amount_per_day == 0.0
    assert c.max_daily_loss == 0.0
    assert c.trading_enabled is True


def test_effective_leverage_is_band_max():
    assert effective_leverage(Config(leverage_min=10, leverage_max=15)) == 15
    assert effective_leverage(Config(leverage_min=1, leverage_max=8)) == 8
    # if misconfigured (min > max), the floor wins
    assert effective_leverage(Config(leverage_min=12, leverage_max=8)) == 12


def test_from_env_overrides(monkeypatch):
    monkeypatch.setenv("HT_LEVERAGE_MIN", "8")
    monkeypatch.setenv("HT_LEVERAGE_MAX", "12")
    monkeypatch.setenv("HT_MAX_TRADE_PER_DAY", "2500")
    monkeypatch.setenv("HT_MAX_DAILY_LOSS", "800")
    monkeypatch.setenv("HT_TRADING_ENABLED", "false")
    c = from_env(Config(), dotenv_path=MISSING)
    assert c.leverage_min == 8.0 and c.leverage_max == 12.0
    assert effective_leverage(c) == 12.0
    assert c.max_trade_amount_per_day == 2500.0
    assert c.max_daily_loss == 800.0
    assert c.trading_enabled is False


def test_from_env_ai_flags(monkeypatch):
    monkeypatch.setenv("AI_CLAUDE_CODE_IS_ENABLED", "true")
    monkeypatch.setenv("AI_CLAUDE_CODE_POLL_IN_MIN", "45")
    monkeypatch.setenv("AI_ANTHROPIC_IS_ENABLED", "false")
    monkeypatch.setenv("AI_ANTHROPIC_POLL_IN_MIN", "10")
    c = from_env(Config(), dotenv_path=MISSING)
    assert c.ai_claude_code_enabled is True and c.ai_claude_code_poll_min == 45
    assert c.ai_anthropic_enabled is False and c.ai_anthropic_poll_min == 10


def test_from_env_keeps_defaults_when_unset(monkeypatch):
    for k in ("HT_LEVERAGE_MIN", "HT_LEVERAGE_MAX", "HT_MAX_TRADE_PER_DAY",
              "HT_MAX_DAILY_LOSS", "HT_TRADING_ENABLED", "HT_ALERT_MODE"):
        monkeypatch.delenv(k, raising=False)
    c = from_env(Config(), dotenv_path=MISSING)
    assert c.leverage_max == 15.0 and c.trading_enabled is True and c.alert_mode == "console"
