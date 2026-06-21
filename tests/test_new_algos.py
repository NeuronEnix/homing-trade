from homing_trade.skills.indicators import macd, bollinger
from homing_trade.skills.macd import MacdCross
from homing_trade.skills.bollinger import BollingerRevert
from homing_trade.skills.donchian import DonchianBreakout
from homing_trade.models import Candle, Position


def cf(prices, span=1.0):
    return [Candle(open=p, high=p + span, low=p - span, close=p, volume=1.0, time=1000 + i * 60000)
            for i, p in enumerate(prices)]


def long_pos(name):
    return Position(strategy=name, side="LONG", entry_price=100, size=1, leverage=15,
                    margin=1, stop_price=98, opened_at=0)


# --- indicators ---
def test_macd_indicator_short_returns_none():
    assert macd([1, 2, 3]) == (None, None)


def test_macd_indicator_returns_numbers():
    m, s = macd([float(x) for x in range(1, 60)])
    assert m is not None and s is not None


def test_bollinger_flat_series_bands_collapse():
    mid, up, lo = bollinger([100.0] * 20)
    assert mid == 100.0 and up == 100.0 and lo == 100.0


def test_bollinger_short_returns_none():
    assert bollinger([1, 2, 3], 20) == (None, None, None)


# --- MACD skill ---
def test_macd_warmup_holds():
    assert MacdCross().on_candle(cf([1, 2, 3]), None).action == "HOLD"


def test_macd_emits_indicators_when_warm():
    sig = MacdCross().on_candle(cf([float(x) for x in range(1, 60)]), None)
    assert "macd" in sig.indicators


# --- Bollinger skill ---
def test_bollinger_long_below_lower_band():
    sig = BollingerRevert().on_candle(cf([100.0] * 20 + [90.0]), None)
    assert sig.action == "LONG"


def test_bollinger_close_at_mean():
    sig = BollingerRevert().on_candle(cf([90.0] * 19 + [100.0]), long_pos("bollinger"))
    assert sig.action == "CLOSE"


# --- Donchian skill ---
def test_donchian_breakout_long():
    sig = DonchianBreakout(period=20).on_candle(cf([99.0] * 20 + [105.0]), None)
    assert sig.action == "LONG"


def test_donchian_breakdown_close():
    sig = DonchianBreakout(period=20).on_candle(cf([101.0] * 20 + [95.0]), long_pos("donchian"))
    assert sig.action == "CLOSE"


def test_donchian_warmup_holds():
    assert DonchianBreakout(period=20).on_candle(cf([100.0] * 5), None).action == "HOLD"
