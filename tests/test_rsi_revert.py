# tests/test_rsi_revert.py
from homing_trade.skills.rsi_revert import RsiRevert
from homing_trade.models import Candle, Position


def candles_from(closes):
    return [Candle(open=c, high=c, low=c, close=c, volume=1, time=i)
            for i, c in enumerate(closes)]


def test_warming_up_returns_hold():
    s = RsiRevert(period=14)
    sig = s.on_candle(candles_from([1, 2, 3]), None)
    assert sig.action == "HOLD"


def test_oversold_opens_long():
    s = RsiRevert(period=14)
    closes = [float(x) for x in range(40, 9, -1)]  # strictly falling -> RSI very low
    sig = s.on_candle(candles_from(closes), None)
    assert sig.action == "LONG"


def test_overbought_closes_long():
    s = RsiRevert(period=14)
    closes = [float(x) for x in range(1, 32)]  # strictly rising -> RSI very high
    pos = Position(strategy="rsi_revert", side="LONG", entry_price=10, size=1,
                   leverage=3, margin=3, stop_price=9, opened_at=0)
    sig = s.on_candle(candles_from(closes), pos)
    assert sig.action == "CLOSE"


def test_rising_no_position_holds():
    s = RsiRevert(period=14)
    closes = [float(x) for x in range(1, 32)]
    sig = s.on_candle(candles_from(closes), None)
    assert sig.action == "HOLD"
