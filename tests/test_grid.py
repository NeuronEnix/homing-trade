# tests/test_grid.py
from homing_trade.skills.grid import Grid
from homing_trade.models import Candle, Position


def candles_from(closes):
    return [Candle(open=c, high=c, low=c, close=c, volume=1, time=i)
            for i, c in enumerate(closes)]


def test_warming_up_returns_hold():
    s = Grid(levels=5, band_pct=0.02, lookback=60)
    sig = s.on_candle(candles_from([100] * 10), None)
    assert sig.action == "HOLD"


def test_buy_at_bottom_of_band():
    s = Grid(levels=5, band_pct=0.02, lookback=10)
    closes = [100.0] * 9 + [97.0]  # last price below ref*(1-0.02)=98
    sig = s.on_candle(candles_from(closes), None)
    assert sig.action == "LONG"


def test_close_at_top_of_band():
    s = Grid(levels=5, band_pct=0.02, lookback=10)
    closes = [100.0] * 9 + [103.0]  # last price above ref*(1+0.02)=102
    pos = Position(strategy="grid", side="LONG", entry_price=98, size=1,
                   leverage=3, margin=33, stop_price=96, opened_at=0)
    sig = s.on_candle(candles_from(closes), pos)
    assert sig.action == "CLOSE"


def test_middle_holds():
    s = Grid(levels=5, band_pct=0.02, lookback=10)
    closes = [100.0] * 10
    sig = s.on_candle(candles_from(closes), None)
    assert sig.action == "HOLD"
