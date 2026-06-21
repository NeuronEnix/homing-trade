from homing_trade.config import Config, from_env, effective_leverage

MISSING = "/no/such/.env"  # isolate tests from the real repo .env


def test_futures_and_leverage_defaults():
    c = Config()
    assert c.pair_candles == "B-BTC_USDT"   # futures perpetual, not INR spot
    assert c.leverage == 15.0
    assert c.leverage_min == 1.0 and c.leverage_max == 15.0
    assert c.max_trade_amount_per_day == 0.0
    assert c.max_daily_loss == 0.0
    assert c.trading_enabled is True


def test_effective_leverage_clamps():
    assert effective_leverage(Config(leverage=15, leverage_min=1, leverage_max=15)) == 15
    assert effective_leverage(Config(leverage=50, leverage_min=1, leverage_max=15)) == 15
    assert effective_leverage(Config(leverage=0.5, leverage_min=1, leverage_max=15)) == 1


def test_from_env_overrides(monkeypatch):
    monkeypatch.setenv("HOMING_LEVERAGE", "10")
    monkeypatch.setenv("HOMING_MAX_TRADE_PER_DAY", "2500")
    monkeypatch.setenv("HOMING_MAX_DAILY_LOSS", "800")
    monkeypatch.setenv("HOMING_TRADING_ENABLED", "false")
    c = from_env(Config(), dotenv_path=MISSING)
    assert c.leverage == 10.0
    assert c.max_trade_amount_per_day == 2500.0
    assert c.max_daily_loss == 800.0
    assert c.trading_enabled is False


def test_from_env_keeps_defaults_when_unset(monkeypatch):
    for k in ("HOMING_LEVERAGE", "HOMING_MAX_TRADE_PER_DAY", "HOMING_MAX_DAILY_LOSS",
              "HOMING_TRADING_ENABLED", "HOMING_ALERT_MODE"):
        monkeypatch.delenv(k, raising=False)
    c = from_env(Config(), dotenv_path=MISSING)
    assert c.leverage == 15.0 and c.trading_enabled is True and c.alert_mode == "console"
