"""Phase 7 #2: z-score mean-reversion candidate strategy.

LONG when price is `z_entry` sample-std below its rolling mean (oversold); CLOSE a long once it
reverts back toward the mean (z >= -z_exit). Long-only, mirroring the Bollinger reverter. Tests cover
warm-up HOLD, the oversold entry, the revert exit, the flat-series no-trade guard, that an extreme
price without a position-or-entry just HOLDs, and registry wiring."""
from homing_trade.skills.zscore_revert import ZScoreRevert
from homing_trade.engine import build_skills
from homing_trade.models import Candle, Position


def cf(prices, span=1.0):
    return [Candle(open=p, high=p + span, low=p - span, close=p, volume=1.0, time=1000 + i * 60000)
            for i, p in enumerate(prices)]


def long_pos(name="zscore_revert"):
    return Position(strategy=name, side="LONG", entry_price=100, size=1, leverage=15,
                    margin=1, stop_price=98, opened_at=0)


def test_zscore_warmup_holds():
    assert ZScoreRevert(period=20).on_candle(cf([100.0] * 5), None).action == "HOLD"


def test_zscore_flat_series_no_trade():
    sig = ZScoreRevert(period=20).on_candle(cf([100.0] * 25), None)
    assert sig.action == "HOLD"
    assert sig.indicators["z"] == 0.0


def test_zscore_oversold_enters_long():
    sig = ZScoreRevert(period=20, z_entry=2.0).on_candle(cf([100.0] * 19 + [90.0]), None)
    assert sig.action == "LONG"
    assert sig.indicators["z"] < -2.0


def test_zscore_reverts_closes_long():
    # descending ramp: last price is ~1.6 std below the mean — no longer 2-std oversold, so a long
    # exits. Non-vacuous: z lands strictly inside (-z_exit, 0), exercising the exit threshold.
    sig = ZScoreRevert(period=20, z_exit=2.0).on_candle(cf([float(p) for p in range(100, 80, -1)]),
                                                         long_pos())
    assert sig.action == "CLOSE"
    assert -2.0 < sig.indicators["z"] < 0.0


def test_zscore_extreme_without_position_and_no_entry_holds():
    # mildly below mean (within z_entry) and no position -> HOLD, no spurious trade
    sig = ZScoreRevert(period=20, z_entry=5.0).on_candle(cf([100.0] * 19 + [90.0]), None)
    assert sig.action == "HOLD"


def test_zscore_registered_in_factory():
    skills = build_skills(["zscore_revert"])
    assert len(skills) == 1 and skills[0].name == "zscore_revert"
