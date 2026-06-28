# tests/test_macd.py
from homing_trade.skills.macd import MacdCross
from homing_trade.models import Candle, Position


def candles_from(closes):
    return [Candle(open=c, high=c, low=c, close=c, volume=1, time=i)
            for i, c in enumerate(closes)]


def test_warming_up_returns_hold():
    s = MacdCross(fast=3, slow=6, signal=3)
    assert s.on_candle(candles_from([1, 2, 3]), None).action == "HOLD"


def test_bullish_cross_from_flat_opens_long():
    s = MacdCross(fast=3, slow=6, signal=3)
    closes = [float(x) for x in range(40, 1, -1)] + [10.0]  # downtrend then sharp up
    assert s.on_candle(candles_from(closes), None).action == "LONG"


def test_bearish_cross_from_flat_opens_short():
    # Futures: a bearish MACD cross with no position must SHORT the downtrend,
    # not sit in HOLD. This is the long-only-bias fix.
    s = MacdCross(fast=3, slow=6, signal=3)
    closes = [float(x) for x in range(1, 40)] + [30.0]  # uptrend then sharp drop -> bearish cross
    assert s.on_candle(candles_from(closes), None).action == "SHORT"


def test_bearish_cross_while_long_reverses_to_short():
    # Holding a long, a bearish cross emits SHORT; the engine closes the long and flips.
    s = MacdCross(fast=3, slow=6, signal=3)
    closes = [float(x) for x in range(1, 40)] + [30.0]
    pos = Position(strategy="macd", side="LONG", entry_price=39, size=1,
                   leverage=10, margin=4, stop_price=37, opened_at=0)
    assert s.on_candle(candles_from(closes), pos).action == "SHORT"
