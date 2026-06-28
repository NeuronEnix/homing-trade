# tests/test_donchian.py
from homing_trade.skills.donchian import DonchianBreakout
from homing_trade.models import Candle, Position


def candles_from(closes):
    return [Candle(open=c, high=c, low=c, close=c, volume=1, time=i)
            for i, c in enumerate(closes)]


def test_warming_up_returns_hold():
    s = DonchianBreakout(period=20)
    assert s.on_candle(candles_from([100.0] * 5), None).action == "HOLD"


def test_breakout_up_from_flat_opens_long():
    s = DonchianBreakout(period=20)
    closes = [100.0] * 25 + [110.0]  # break above prior high
    assert s.on_candle(candles_from(closes), None).action == "LONG"


def test_breakdown_from_flat_opens_short():
    # Futures: a break below the prior-period low with no position must SHORT,
    # not sit in HOLD. Long-only-bias fix.
    s = DonchianBreakout(period=20)
    closes = [100.0] * 25 + [90.0]  # break below prior low
    assert s.on_candle(candles_from(closes), None).action == "SHORT"


def test_breakdown_while_long_reverses_to_short():
    s = DonchianBreakout(period=20)
    closes = [100.0] * 25 + [90.0]
    pos = Position(strategy="donchian", side="LONG", entry_price=100, size=1,
                   leverage=10, margin=10, stop_price=98, opened_at=0)
    assert s.on_candle(candles_from(closes), pos).action == "SHORT"
