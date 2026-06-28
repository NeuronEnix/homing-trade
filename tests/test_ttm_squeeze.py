"""Phase 7 #2: TTM Squeeze candidate strategy.

Squeeze ON = Bollinger Bands inside Keltner Channels (compressed volatility); RELEASE = bands expand
back out. Trade the release in the direction of momentum (close vs BB midline): LONG on a release with
positive momentum, CLOSE a long when the squeeze re-engages or momentum turns down. Long-only. Tests
use period=5 to keep the engineered squeeze/release windows small + deterministic."""
from homing_trade.skills.ttm_squeeze import TtmSqueeze, squeeze_state
from homing_trade.engine import build_skills
from homing_trade.models import Candle, Position


def cf(prices, span=1.0):
    return [Candle(open=p, high=p + span, low=p - span, close=p, volume=1.0, time=1000 + i * 60000)
            for i, p in enumerate(prices)]


def long_pos(name="ttm_squeeze"):
    return Position(strategy=name, side="LONG", entry_price=100, size=1, leverage=15,
                    margin=1, stop_price=98, opened_at=0)


def test_squeeze_state_flat_series_is_on():
    on, mom = squeeze_state(cf([100.0] * 10), period=5)
    assert on is True and mom == 0.0


def test_ttm_warmup_holds():
    assert TtmSqueeze(period=5).on_candle(cf([100.0] * 3), None).action == "HOLD"


def test_ttm_release_up_enters_long():
    # 8 flat bars (squeeze on) then a single upside burst on the LAST bar => release with mom>0
    sig = TtmSqueeze(period=5).on_candle(cf([100.0] * 8 + [108.0]), None)
    assert sig.action == "LONG"
    assert sig.indicators["squeeze"] is False and sig.indicators["momentum"] > 0


def test_ttm_release_down_enters_short():
    # release with negative momentum -> SHORT the breakout (symmetric; was long-only HOLD)
    sig = TtmSqueeze(period=5).on_candle(cf([100.0] * 8 + [92.0]), None)
    assert sig.action == "SHORT"
    assert sig.indicators["momentum"] < 0


def test_ttm_reengage_closes_long():
    # holding a long while the series is compressed (squeeze on) -> exit
    sig = TtmSqueeze(period=5).on_candle(cf([100.0] * 10), long_pos())
    assert sig.action == "CLOSE"
    assert sig.indicators["squeeze"] is True


def test_ttm_registered_in_factory():
    skills = build_skills(["ttm_squeeze"])
    assert len(skills) == 1 and skills[0].name == "ttm_squeeze"
