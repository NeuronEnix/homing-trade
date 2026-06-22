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
    # AI traders — two INDEPENDENT Claude brains, each toggled + paced on its own.
    # If both are enabled they run side by side (separate wallets) so you can compare them.
    ai_claude_code_enabled: bool = False    # backend: local `claude` CLI (uses Claude Code, no API key)
    ai_claude_code_poll_sec: int = 3600     # how often it consults Claude (seconds)
    ai_anthropic_enabled: bool = False      # backend: Anthropic API (needs ANTHROPIC_API_KEY)
    ai_anthropic_poll_sec: int = 900
    # Bird's-eye charts fed to the AI each consult (higher timeframes for context); the AI
    # narrows down by requesting lower timeframes (5m/1m) via requested_charts when it sees a setup.
    ai_timeframes: list[str] = field(default_factory=lambda: ["15m", "1h", "4h"])
    ai_chart_limit: int = 150               # candles per chart
    # External research signals (Phase 6). All free + keyless, cached + degrade to "unavailable" so
    # they never block a consult. Default ON; mute via FNG_IS_ENABLED / DERIVS_IS_ENABLED = false.
    fng_enabled: bool = True
    derivs_enabled: bool = True             # Binance perp funding-rate + open-interest
    coindcx_signal_enabled: bool = True     # CoinDCX live orderbook + mark/funding (traded instrument)
    price_ref_enabled: bool = True          # CoinGecko independent reference price (venue sanity-check)
    coingecko_key_env: str = "COINGECKO_DEMO_KEY"   # free Demo key (optional; keyless tier works)
    # Snapshot of the AI_* environment captured by from_env (the single env->Config layer). The
    # multi-AI provider registry (ai_traders.build_ai_traders) discovers AI_<NAME>_IS_ENABLED/
    # _BACKEND/_POLL_IN_SEC/_MODEL providers from THIS dict, never the live os.environ — so a bare
    # Config() composes deterministically (empty => no env-discovered providers).
    ai_providers_env: dict = field(default_factory=dict)
    # Reflection — the periodic learn->correct loop. Default OFF (and free): when enabled it
    # consults Claude on a slow cadence to retrospect over completed trades and FILE human-gated
    # playbook proposals (it never applies anything itself; the approval gate still stands).
    reflection_enabled: bool = False
    reflection_poll_sec: int = 3600         # how often the periodic reflection runs (seconds)
    reflection_min_trades: int = 5          # need >= this many fresh outcomes before reflecting
    reflection_backend: str = "cli"         # "cli" (claude headless, no API key) | "api"
    reflection_model: str = ""              # defaults to llm_model when empty
    reflection_cli_timeout: int = 180
    reflection_max_tokens: int = 800
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
    web_port: int = 8787
    price_symbols: list[str] = field(
        default_factory=lambda: ["BTCUSDT", "ETHUSDT", "PAXGUSDT"]  # shown live in the UI
    )


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

    def _i(name, cur):
        v = os.environ.get(name)
        return int(float(v)) if v not in (None, "") else cur

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
        ai_claude_code_enabled=_b("AI_CLAUDE_CODE_IS_ENABLED", cfg.ai_claude_code_enabled),
        ai_claude_code_poll_sec=_i("AI_CLAUDE_CODE_POLL_IN_SEC", cfg.ai_claude_code_poll_sec),
        ai_anthropic_enabled=_b("AI_ANTHROPIC_IS_ENABLED", cfg.ai_anthropic_enabled),
        ai_anthropic_poll_sec=_i("AI_ANTHROPIC_POLL_IN_SEC", cfg.ai_anthropic_poll_sec),
        ai_timeframes=_list("HT_AI_TIMEFRAMES", cfg.ai_timeframes),
        ai_chart_limit=_i("HT_AI_CHART_LIMIT", cfg.ai_chart_limit),
        fng_enabled=_b("FNG_IS_ENABLED", cfg.fng_enabled),
        derivs_enabled=_b("DERIVS_IS_ENABLED", cfg.derivs_enabled),
        coindcx_signal_enabled=_b("COINDCX_SIGNAL_IS_ENABLED", cfg.coindcx_signal_enabled),
        price_ref_enabled=_b("PRICE_REF_IS_ENABLED", cfg.price_ref_enabled),
        # Capture the AI_* env subset so build_ai_traders discovers providers from Config, not the
        # live os.environ — keeps env parsing in this single layer and engine composition deterministic.
        ai_providers_env={k: v for k, v in os.environ.items() if k.startswith("AI_")},
        reflection_enabled=_b("REFLECTION_IS_ENABLED", cfg.reflection_enabled),
        reflection_poll_sec=_i("REFLECTION_POLL_IN_SEC", cfg.reflection_poll_sec),
        reflection_min_trades=_i("REFLECTION_MIN_TRADES", cfg.reflection_min_trades),
        reflection_backend=_s("REFLECTION_BACKEND", cfg.reflection_backend),
        reflection_model=_s("REFLECTION_MODEL", cfg.reflection_model),
        reflection_cli_timeout=_i("REFLECTION_CLI_TIMEOUT", cfg.reflection_cli_timeout),
        reflection_max_tokens=_i("REFLECTION_MAX_TOKENS", cfg.reflection_max_tokens),
        enabled_skills=_list("HT_SKILLS", cfg.enabled_skills),
    )


def effective_leverage(cfg):
    """The leverage the bot trades at: the top of the configured band
    (leverage_max), never below leverage_min."""
    return max(getattr(cfg, "leverage_min", 1.0), getattr(cfg, "leverage_max", cfg.leverage))
