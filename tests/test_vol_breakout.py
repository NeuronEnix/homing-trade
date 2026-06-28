"""Phase 7 #2: volume-confirmed breakout candidate strategy.

A Donchian-style breakout gated on volume: LONG only when price breaks the prior-period high AND the
breakout candle's volume >= vol_mult x the prior average; CLOSE a long on a break below the prior low.
Tests cover warm-up, the confirmed breakout, the volume-unconfirmed breakout (HOLD, no entry), the
zero-volume guard, the breakdown close, and registry wiring."""
from homing_trade.skills.vol_breakout import VolumeBreakout
from homing_trade.engine import build_skills
from homing_trade.models import Candle, Position


def cfv(prices, vols, span=1.0):
    return [Candle(open=p, high=p + span, low=p - span, close=p, volume=v, time=1000 + i * 60000)
            for i, (p, v) in enumerate(zip(prices, vols))]


def long_pos(name="vol_breakout"):
    return Position(strategy=name, side="LONG", entry_price=100, size=1, leverage=15,
                    margin=1, stop_price=98, opened_at=0)


def test_vol_breakout_warmup_holds():
    sig = VolumeBreakout(period=20).on_candle(cfv([100.0] * 5, [1.0] * 5), None)
    assert sig.action == "HOLD"


def test_vol_breakout_confirmed_enters_long():
    sig = VolumeBreakout(period=20, vol_mult=1.5).on_candle(
        cfv([100.0] * 20 + [110.0], [1.0] * 20 + [2.0]), None)
    assert sig.action == "LONG"
    assert sig.indicators["vol"] >= 1.5 * sig.indicators["avg_vol"]


def test_vol_breakout_unconfirmed_holds():
    # price breaks out but volume is only avg -> not confirmed -> no entry
    sig = VolumeBreakout(period=20, vol_mult=1.5).on_candle(
        cfv([100.0] * 20 + [110.0], [1.0] * 20 + [1.0]), None)
    assert sig.action == "HOLD"
    assert "unconfirmed" in sig.reason


def test_vol_breakout_zero_avg_volume_never_enters():
    sig = VolumeBreakout(period=20, vol_mult=1.5).on_candle(
        cfv([100.0] * 20 + [110.0], [0.0] * 20 + [5.0]), None)
    assert sig.action == "HOLD"            # avg_vol == 0 -> vol_ok False, no spurious breakout


def test_vol_breakout_confirmed_breakdown_reverses_to_short():
    # a volume-confirmed break BELOW the prior low reverses a long into a short (symmetric).
    sig = VolumeBreakout(period=20, vol_mult=1.5).on_candle(
        cfv([100.0] * 20 + [90.0], [1.0] * 20 + [2.0]), long_pos())
    assert sig.action == "SHORT"


def test_vol_breakout_confirmed_breakdown_from_flat_enters_short():
    sig = VolumeBreakout(period=20, vol_mult=1.5).on_candle(
        cfv([100.0] * 20 + [90.0], [1.0] * 20 + [2.0]), None)
    assert sig.action == "SHORT"


def test_vol_breakout_unconfirmed_breakdown_holds():
    # break below the low but volume only average -> unconfirmed -> no short
    sig = VolumeBreakout(period=20, vol_mult=1.5).on_candle(
        cfv([100.0] * 20 + [90.0], [1.0] * 21), None)
    assert sig.action == "HOLD"


def test_vol_breakout_registered_in_factory():
    skills = build_skills(["vol_breakout"])
    assert len(skills) == 1 and skills[0].name == "vol_breakout"
