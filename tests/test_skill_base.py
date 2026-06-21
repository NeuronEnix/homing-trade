import pytest
from algotrading.skills.base import Strategy
from algotrading.models import Signal


class Dummy(Strategy):
    name = "dummy"

    def on_candle(self, candles, position):
        return Signal(action="HOLD", reason="dummy")


def test_cannot_instantiate_abstract():
    with pytest.raises(TypeError):
        Strategy()


def test_subclass_returns_signal():
    s = Dummy()
    sig = s.on_candle([], None)
    assert isinstance(sig, Signal)
    assert sig.action == "HOLD"
    assert s.name == "dummy"
