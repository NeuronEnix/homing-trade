from dataclasses import dataclass, field


@dataclass
class Config:
    # FUTURES ONLY — CoinDCX INR-margin futures. The contract is the USDT-quoted
    # perpetual (B-BTC_USDT); margin/settlement is INR. No spot, no options.
    pair_candles: str = "B-BTC_USDT"   # futures perpetual instrument
    ticker_market: str = "BTCINR"
    usdt_inr_rate: float = 88.0        # USDT->INR (for absolute INR figures / live)
    interval: str = "15m"              # backtests show 1m/5m overtrade & bleed fees; 15m is the sweet spot
    poll_seconds: int = 60
    starting_balance: float = 5000.0
    leverage: float = 15.0             # default 15x (futures); bounded by min/max below
    leverage_min: float = 1.0
    leverage_max: float = 15.0
    max_trade_amount_per_day: float = 0.0   # 0 = no cap; INR notional opened per day
    max_daily_loss: float = 0.0             # 0 = no kill switch; halt if day's loss >= this (INR)
    trading_enabled: bool = True            # master switch — set False to stop trading immediately
    fee: float = 0.0005       # 0.05% per side
    slippage: float = 0.0005  # 5 bps
    risk_pct: float = 0.02    # max loss fraction of balance per trade
    stop_pct: float = 0.02    # stop distance as fraction of entry price
    db_path: str = "data/paper_trading.db"
    enabled_skills: list[str] = field(
        default_factory=lambda: ["ma_trend", "rsi_revert", "grid", "macd", "bollinger", "donchian"]
    )
    agent_mode: str = "heuristic"           # "heuristic" | "llm"
    llm_model: str = "claude-opus-4-8"
    llm_interval_min: int = 15              # LlmTrader consults Claude every N minutes
    llm_backend: str = "cli"                # "cli" (claude headless, no API key) | "api" (anthropic SDK)
    rl_alpha: float = 0.1
    rl_gamma: float = 0.95
    rl_epsilon: float = 0.1
    rl_fast: int = 9
    rl_slow: int = 21
    committee_threshold: float = 0.2
    risk_vol_window: int = 20
    risk_vol_threshold: float = 0.04
    allocator_enabled: bool = False
    allocator_lookback: int = 20
    qtable_dir: str = "data"
    alert_mode: str = "console"          # "console" | "file" | "webhook" | "telegram" | "null"
    alert_log_path: str = "data/alerts.log"
    webhook_url: str = ""
    discord_webhook_env: str = "DISCORD_WEBHOOK_URL"
    telegram_token_env: str = "TELEGRAM_BOT_TOKEN"
    telegram_chat_id_env: str = "TELEGRAM_CHAT_ID"
    live_enabled: bool = False
    live_dry_run: bool = True
    coindcx_key_env: str = "COINDCX_API_KEY"
    coindcx_secret_env: str = "COINDCX_API_SECRET"
    daemon_status_path: str = "data/daemon_status.json"
    daemon_backoff_seconds: int = 5


CONFIG = Config()


def from_env(base=None, *, dotenv_path=".env"):
    """Return a Config with `.env` / environment overrides applied.

    Reads `.env` (gitignored) then HT_* environment variables. Lets you tune the
    leverage band and risk limits without touching code:
        HT_LEVERAGE_MIN, HT_LEVERAGE_MAX,
        HT_MAX_TRADE_PER_DAY, HT_MAX_DAILY_LOSS, HT_TRADING_ENABLED,
        HT_ALERT_MODE, HT_USDT_INR.
    (There is no single HT_LEVERAGE — the bot trades at the max of the band.)
    """
    import os
    from dataclasses import replace
    from homing_trade.dotenv import load_dotenv
    cfg = base or CONFIG
    load_dotenv(dotenv_path)

    def _f(name, cur):
        v = os.environ.get(name)
        return float(v) if v not in (None, "") else cur

    def _b(name, cur):
        v = os.environ.get(name)
        if v in (None, ""):
            return cur
        return v.strip().lower() in ("1", "true", "yes", "on")

    def _s(name, cur):
        v = os.environ.get(name)
        return v if v not in (None, "") else cur

    def _list(name, cur):
        v = os.environ.get(name)
        if v in (None, ""):
            return cur
        return [s.strip() for s in v.split(",") if s.strip()]

    return replace(
        cfg,
        leverage_min=_f("HT_LEVERAGE_MIN", cfg.leverage_min),
        leverage_max=_f("HT_LEVERAGE_MAX", cfg.leverage_max),
        max_trade_amount_per_day=_f("HT_MAX_TRADE_PER_DAY", cfg.max_trade_amount_per_day),
        max_daily_loss=_f("HT_MAX_DAILY_LOSS", cfg.max_daily_loss),
        trading_enabled=_b("HT_TRADING_ENABLED", cfg.trading_enabled),
        usdt_inr_rate=_f("HT_USDT_INR", cfg.usdt_inr_rate),
        alert_mode=_s("HT_ALERT_MODE", cfg.alert_mode),
        llm_model=_s("HT_LLM_MODEL", cfg.llm_model),
        llm_backend=_s("HT_LLM_BACKEND", cfg.llm_backend),
        enabled_skills=_list("HT_SKILLS", cfg.enabled_skills),
    )


def effective_leverage(cfg):
    """The leverage the bot trades at: the top of the configured band
    (leverage_max), never below leverage_min."""
    return max(getattr(cfg, "leverage_min", 1.0), getattr(cfg, "leverage_max", cfg.leverage))
