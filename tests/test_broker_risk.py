from algotrading.broker import Broker
from algotrading.models import Position, Candle

B = Broker(fee=0.0005, slippage=0.0005)


def long_pos():
    return Position(strategy="x", side="LONG", entry_price=100.0, size=1.0,
                    leverage=3.0, margin=33.3, stop_price=98.0, opened_at=0)


def short_pos():
    return Position(strategy="x", side="SHORT", entry_price=100.0, size=1.0,
                    leverage=3.0, margin=33.3, stop_price=102.0, opened_at=0)


def candle(high, low):
    return Candle(open=100, high=high, low=low, close=100, volume=1, time=0)


def test_liquidation_price_long():
    assert round(B.liquidation_price(long_pos()), 4) == round(100 * (1 - 1/3), 4)


def test_liquidation_price_short():
    assert round(B.liquidation_price(short_pos()), 4) == round(100 * (1 + 1/3), 4)


def test_hit_stop_long_true_when_low_breaches():
    assert B.hit_stop(long_pos(), candle(high=101, low=97.5)) is True


def test_hit_stop_long_false_when_above():
    assert B.hit_stop(long_pos(), candle(high=101, low=99)) is False


def test_hit_stop_short_true_when_high_breaches():
    assert B.hit_stop(short_pos(), candle(high=103, low=99)) is True


def test_hit_liquidation_long():
    assert B.hit_liquidation(long_pos(), candle(high=100, low=66.0)) is True
    assert B.hit_liquidation(long_pos(), candle(high=100, low=70.0)) is False


def test_hit_liquidation_short():
    assert B.hit_liquidation(short_pos(), candle(high=134.0, low=100)) is True
    assert B.hit_liquidation(short_pos(), candle(high=130.0, low=100)) is False
