from dataclasses import dataclass, field


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: float
    time: int  # epoch milliseconds


@dataclass
class Signal:
    action: str            # "LONG" | "SHORT" | "CLOSE" | "HOLD"
    confidence: float = 0.0
    reason: str = ""
    indicators: dict = field(default_factory=dict)


@dataclass
class Position:
    strategy: str
    side: str              # "LONG" | "SHORT"
    entry_price: float
    size: float            # units of base asset (BTC)
    leverage: float
    margin: float          # INR margin locked
    stop_price: float
    opened_at: int         # epoch ms
    id: int | None = None
    status: str = "open"   # "open" | "closed"
