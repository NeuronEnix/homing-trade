# tests/test_engine_allocator.py
from algotrading.engine import process_tick
from algotrading.broker import Broker
from algotrading.ledger import MemoryLedger
from algotrading.config import Config
from algotrading.models import Candle


def candles(prices):
    return [Candle(open=p, high=p + 1, low=p - 1, close=p, volume=1.0, time=1000 + i * 60000)
            for i, p in enumerate(prices)]


def _force_long_window():
    # rising then a tick that triggers ma_trend LONG is hard to guarantee; instead use a
    # MemoryLedger and a skill whose signal we control via a stub.
    from algotrading.skills.base import Strategy
    from algotrading.models import Signal

    class AlwaysLong(Strategy):
        name = "ma_trend"
        def on_candle(self, cs, pos):
            return Signal("LONG") if pos is None else Signal("HOLD")
    return AlwaysLong()


def _force_long_window2():
    # A second stub skill (named rsi_revert) that also goes long immediately.
    # Used alongside _force_long_window() so that with two strategies the allocator
    # assigns each weight=0.55 (<1), producing a strictly smaller position than weight=1.
    from algotrading.skills.base import Strategy
    from algotrading.models import Signal

    class AlwaysLong2(Strategy):
        name = "rsi_revert"
        def on_candle(self, cs, pos):
            return Signal("LONG") if pos is None else Signal("HOLD")
    return AlwaysLong2()


def test_allocator_weight_scaling():
    cfg = Config(allocator_enabled=False)
    led = MemoryLedger("ma_trend", 5000.0)
    broker = Broker(cfg.fee, cfg.slippage)
    skill = _force_long_window()
    process_tick(led, broker, [skill], candles([100.0] * 30), cfg)
    pos = led.get_open_position("ma_trend")
    assert pos is not None  # opened; with full risk_pct sizing
    size_full = pos.size
    # same again with allocator enabled but no trade history -> perf 0 for both strategies
    # with two strategies: each gets weight = floor + (cap-floor)*0.5 = 0.55 < 1 -> smaller risk
    cfg2 = Config(allocator_enabled=True)
    led2 = MemoryLedger("ma_trend", 5000.0)
    led2.ensure_strategy("rsi_revert", 5000.0)
    process_tick(led2, broker, [_force_long_window(), _force_long_window2()], candles([100.0] * 30), cfg2)
    pos2 = led2.get_open_position("ma_trend")
    assert pos2 is not None
    # with two strategies and floor<1, each allocator weight = 0.55 < 1 -> strictly smaller size
    assert pos2.size < size_full
