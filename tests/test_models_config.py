from homing_trade.models import Candle, Signal, Position
from homing_trade.config import CONFIG


def test_candle_fields():
    c = Candle(open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0, time=1000)
    assert c.close == 1.5 and c.time == 1000


def test_signal_defaults():
    s = Signal(action="LONG")
    assert s.action == "LONG"
    assert s.confidence == 0.0
    assert s.reason == ""
    assert s.indicators == {}


def test_position_defaults():
    p = Position(strategy="ma_trend", side="LONG", entry_price=100.0, size=0.5,
                 leverage=3.0, margin=50.0, stop_price=98.0, opened_at=1000)
    assert p.id is None and p.status == "open"


def test_config_values():
    assert CONFIG.starting_balance == 5000.0
    assert CONFIG.leverage == 15.0                 # futures default (bounded by min/max)
    assert CONFIG.fee == 0.0005
    assert CONFIG.pair_candles == "B-BTC_USDT"     # futures perpetual (no INR spot)
    assert "ma_trend" in CONFIG.enabled_skills
