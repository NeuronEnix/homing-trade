"""Phase 7 #2: Supertrend (ATR trend-follower) candidate strategy + the reusable atr() indicator.

Supertrend enters LONG on a flip from down→up and closes a long on the up→down flip. Tests cover the
atr() helper (warm-up None + a deterministic constant-range value), warm-up HOLD, the flip-up entry,
the flip-down exit, that it does NOT re-enter mid-trend (only on a fresh flip), and registry wiring."""
from homing_trade.skills.indicators import atr
from homing_trade.skills.supertrend import Supertrend
from homing_trade.engine import build_skills
from homing_trade.models import Candle, Position


def cf(prices, span=1.0):
    return [Candle(open=p, high=p + span, low=p - span, close=p, volume=1.0, time=1000 + i * 60000)
            for i, p in enumerate(prices)]


def long_pos(name="supertrend"):
    return Position(strategy=name, side="LONG", entry_price=100, size=1, leverage=15,
                    margin=1, stop_price=98, opened_at=0)


# --- atr() indicator ---
def test_atr_short_returns_none():
    assert atr(cf([100.0] * 5), 14) is None


def test_atr_constant_range_is_the_range():
    # cf span=1 -> high=p+1, low=p-1 -> TR == 2.0 every bar -> ATR == 2.0
    assert atr(cf([100.0] * 30), 14) == 2.0


# --- Supertrend skill ---
def test_supertrend_warmup_holds():
    assert Supertrend(period=10).on_candle(cf([100.0] * 4), None).action == "HOLD"


def test_supertrend_flip_up_enters_long():
    # a settled downtrend, then a sharp jump up on the LAST bar forces a down->up flip there
    prices = [float(p) for p in range(160, 80, -1)] + [200.0]
    sig = Supertrend(period=10, mult=3.0).on_candle(cf(prices), None)
    assert sig.action == "LONG"
    assert sig.indicators["trend"] == "up" and "supertrend" in sig.indicators


def test_supertrend_flip_down_closes_long():
    # a settled uptrend, then a sharp drop on the LAST bar forces an up->down flip there
    prices = [float(p) for p in range(80, 180)] + [50.0]
    sig = Supertrend(period=10, mult=3.0).on_candle(cf(prices), long_pos())
    assert sig.action == "CLOSE"
    assert sig.indicators["trend"] == "down"


def test_supertrend_no_reentry_or_close_mid_uptrend():
    # holding LONG with no fresh flip on the LAST bar -> HOLD (no repeat entry, no premature close)
    prices = [float(p) for p in range(80, 200)]
    sig = Supertrend(period=10, mult=3.0).on_candle(cf(prices), long_pos())
    assert sig.action == "HOLD"


def test_supertrend_seed_bar_does_not_trigger_entry():
    # exactly 2 computed bars (len(series)==2) -> still warming up, no entry off the arbitrary seed
    prices = [float(p) for p in range(100, 88, -1)]    # period=10 -> series length 2
    assert Supertrend(period=10, mult=3.0).on_candle(cf(prices), None).action == "HOLD"


def test_supertrend_registered_in_factory():
    skills = build_skills(["supertrend"])
    assert len(skills) == 1 and skills[0].name == "supertrend"
