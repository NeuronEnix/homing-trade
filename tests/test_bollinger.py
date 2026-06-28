# tests/test_bollinger.py — symmetric Bollinger mean-reversion (short the upper band too).
from homing_trade.skills.bollinger import BollingerRevert
from homing_trade.models import Candle, Position


def candles_from(closes):
    return [Candle(open=c, high=c, low=c, close=c, volume=1, time=i)
            for i, c in enumerate(closes)]


def test_long_at_lower_band():
    s = BollingerRevert(period=20, num_std=2.0)
    sig = s.on_candle(candles_from([100.0] * 20 + [90.0]), None)
    assert sig.action == "LONG"


def test_short_at_upper_band_from_flat():
    # Symmetric: an overbought close at/above the upper band SHORTs (was HOLD = long-only).
    s = BollingerRevert(period=20, num_std=2.0)
    sig = s.on_candle(candles_from([100.0] * 20 + [110.0]), None)
    assert sig.action == "SHORT"


def test_close_short_reverts_to_mean():
    s = BollingerRevert(period=20, num_std=2.0)
    closes = [110.0] * 19 + [100.0]  # price falls back to ~mean while short
    pos = Position(strategy="bollinger", side="SHORT", entry_price=112, size=1,
                   leverage=10, margin=10, stop_price=116, opened_at=0)
    sig = s.on_candle(candles_from(closes), pos)
    assert sig.action == "CLOSE"
