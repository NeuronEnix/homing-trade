from dataclasses import dataclass, field


@dataclass
class Config:
    pair_candles: str = "I-BTC_INR"
    ticker_market: str = "BTCINR"
    interval: str = "1m"
    poll_seconds: int = 60
    starting_balance: float = 5000.0
    leverage: float = 3.0
    fee: float = 0.0005       # 0.05% per side
    slippage: float = 0.0005  # 5 bps
    risk_pct: float = 0.02    # max loss fraction of balance per trade
    stop_pct: float = 0.02    # stop distance as fraction of entry price
    db_path: str = "data/paper_trading.db"
    enabled_skills: list[str] = field(
        default_factory=lambda: ["ma_trend", "rsi_revert", "grid"]
    )
    agent_mode: str = "heuristic"           # "heuristic" | "llm"
    llm_model: str = "claude-opus-4-8"
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
    telegram_token_env: str = "TELEGRAM_BOT_TOKEN"
    telegram_chat_id_env: str = "TELEGRAM_CHAT_ID"
    live_enabled: bool = False
    live_dry_run: bool = True
    coindcx_key_env: str = "COINDCX_API_KEY"
    coindcx_secret_env: str = "COINDCX_API_SECRET"
    daemon_status_path: str = "data/daemon_status.json"
    daemon_backoff_seconds: int = 5


CONFIG = Config()
