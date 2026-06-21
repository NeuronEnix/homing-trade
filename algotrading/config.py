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


CONFIG = Config()
