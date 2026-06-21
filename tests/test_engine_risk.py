from homing_trade.engine import process_tick
from homing_trade.broker import Broker
from homing_trade.ledger import MemoryLedger
from homing_trade.risk import DailyRiskGuard
from homing_trade.config import Config, effective_leverage
from homing_trade.skills.base import Strategy
from homing_trade.models import Candle, Signal


class AlwaysLong(Strategy):
    name = "ma_trend"
    def on_candle(self, candles, position):
        return Signal("LONG") if position is None else Signal("HOLD")


def candles():
    return [Candle(open=100, high=101, low=99, close=100, volume=1, time=1000 + i * 60000)
            for i in range(30)]


def test_disabled_guard_blocks_open():
    cfg = Config()
    led = MemoryLedger("ma_trend", 5000.0)
    process_tick(led, Broker(cfg.fee, cfg.slippage), [AlwaysLong()], candles(),
                 cfg, DailyRiskGuard(enabled=False))
    assert led.get_open_position("ma_trend") is None  # master switch blocked it


def test_tiny_daily_cap_blocks_open():
    cfg = Config()
    led = MemoryLedger("ma_trend", 5000.0)
    process_tick(led, Broker(cfg.fee, cfg.slippage), [AlwaysLong()], candles(),
                 cfg, DailyRiskGuard(max_trade_amount_per_day=0.01))
    assert led.get_open_position("ma_trend") is None  # notional >> cap -> blocked


def test_large_cap_allows_open_at_clamped_leverage():
    cfg = Config()  # leverage 15, min 1, max 15
    led = MemoryLedger("ma_trend", 5000.0)
    process_tick(led, Broker(cfg.fee, cfg.slippage), [AlwaysLong()], candles(),
                 cfg, DailyRiskGuard(max_trade_amount_per_day=1e12))
    pos = led.get_open_position("ma_trend")
    assert pos is not None
    assert pos.leverage == effective_leverage(cfg) == 15.0


def test_no_guard_still_opens():
    cfg = Config()
    led = MemoryLedger("ma_trend", 5000.0)
    process_tick(led, Broker(cfg.fee, cfg.slippage), [AlwaysLong()], candles(), cfg)  # guard=None
    assert led.get_open_position("ma_trend") is not None
