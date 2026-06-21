# tests/test_ma_trend.py
from algotrading.skills.ma_trend import MaTrend
from algotrading.models import Candle


def candles_from(closes):
    return [Candle(open=c, high=c, low=c, close=c, volume=1, time=i)
            for i, c in enumerate(closes)]


def test_warming_up_returns_hold():
    s = MaTrend(fast=3, slow=5)
    sig = s.on_candle(candles_from([1, 2, 3]), None)
    assert sig.action == "HOLD"
    assert sig.reason == "warming up"


def test_crossover_up_returns_long():
    s = MaTrend(fast=3, slow=5)
    # downtrend then sharp up so fast EMA crosses above slow on the last candle
    closes = [10, 9, 8, 7, 6, 5, 4, 3, 15]
    sig = s.on_candle(candles_from(closes), None)
    assert sig.action == "LONG"
    assert "fast" in sig.indicators


def test_crossover_down_returns_short():
    s = MaTrend(fast=3, slow=5)
    closes = [3, 4, 5, 6, 7, 8, 9, 10, 1]
    sig = s.on_candle(candles_from(closes), None)
    assert sig.action == "SHORT"
    assert "fast" in sig.indicators
