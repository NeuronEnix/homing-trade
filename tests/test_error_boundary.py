"""Backlog: the per-skill ErrorBoundary (crash isolation + consecutive-failure circuit breaker).

Covers the pure breaker bookkeeping AND its engine integration: a skill that RAISES is isolated (the
rest of the roster keeps trading), counted, and after N consecutive crashes auto-disabled with a
recorded risk_event; a recovering skill's streak resets."""
import pytest

from homing_trade.error_boundary import ErrorBoundary
from homing_trade.engine import process_tick
from homing_trade.broker import Broker
from homing_trade.ledger import MemoryLedger
from homing_trade.config import Config
from homing_trade.skills.base import Strategy
from homing_trade.models import Candle, Signal


# --- the pure breaker ------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [0, -1, 1.5, "3", True, False, None])
def test_threshold_must_be_positive_int(bad):
    with pytest.raises(ValueError):
        ErrorBoundary(bad)


def test_trips_only_on_the_Nth_consecutive_failure():
    eb = ErrorBoundary(threshold=3)
    assert eb.record_failure("s", "e1") is False and not eb.is_tripped("s")
    assert eb.record_failure("s", "e2") is False
    assert eb.record_failure("s", "e3") is True and eb.is_tripped("s")   # newly tripped -> True once
    assert eb.tripped_reason("s") == "e3"


def test_newly_tripped_returns_true_exactly_once():
    eb = ErrorBoundary(threshold=1)
    assert eb.record_failure("s", "boom") is True
    assert eb.record_failure("s", "again") is False     # already tripped -> no-op, not "newly"


def test_success_resets_the_streak_so_a_blip_never_trips():
    eb = ErrorBoundary(threshold=3)
    eb.record_failure("s", "e1")
    eb.record_failure("s", "e2")
    eb.record_success("s")                               # clean run clears the streak
    assert eb.consecutive_failures("s") == 0
    assert eb.record_failure("s", "e3") is False and not eb.is_tripped("s")


def test_reset_re_enables_a_tripped_skill():
    eb = ErrorBoundary(threshold=1)
    eb.record_failure("s", "boom")
    assert eb.is_tripped("s")
    assert eb.reset("s") is True and not eb.is_tripped("s")
    assert eb.reset("s") is False                        # idempotent


def test_breakers_are_per_skill():
    eb = ErrorBoundary(threshold=2)
    eb.record_failure("a", "x")
    eb.record_failure("a", "x")
    assert eb.is_tripped("a") and not eb.is_tripped("b")
    assert eb.tripped_skills() == {"a": "x"}


# --- engine integration ----------------------------------------------------------------------
class Boom(Strategy):
    name = "ma_trend"
    def on_candle(self, candles, position):
        raise RuntimeError("kaboom")


class AlwaysLong(Strategy):
    name = "rsi_revert"
    def on_candle(self, candles, position):
        return Signal("LONG") if position is None else Signal("HOLD")


def _candles():
    return [Candle(open=100, high=101, low=99, close=100, volume=1, time=1000 + i * 60000)
            for i in range(30)]


def _run(led, skills, eb):
    process_tick(led, Broker(Config().fee, Config().slippage), skills, _candles(), Config(),
                 error_boundary=eb)


def test_a_raising_skill_is_isolated_so_the_roster_keeps_trading():
    led = MemoryLedger("ma_trend", 5000.0)
    led.ensure_strategy("rsi_revert", 5000.0)
    eb = ErrorBoundary(threshold=3)
    _run(led, [Boom(), AlwaysLong()], eb)               # Boom raises FIRST in the roster
    assert led.get_open_position("ma_trend") is None     # the crash opened nothing
    assert led.get_open_position("rsi_revert") is not None  # ...but the next skill still traded
    assert eb.consecutive_failures("ma_trend") == 1      # the crash was counted, not propagated


def test_skill_auto_disables_after_threshold_with_a_risk_event():
    led = MemoryLedger("ma_trend", 5000.0)
    eb = ErrorBoundary(threshold=2)
    _run(led, [Boom()], eb)
    _run(led, [Boom()], eb)
    assert eb.is_tripped("ma_trend")
    assert any(e["kind"] == "skill_disabled" and e["strategy"] == "ma_trend"
               for e in led.risk_events)
    # once tripped, on_candle is no longer called (it would raise again if it were)
    before = eb.tripped_reason("ma_trend")
    _run(led, [Boom()], eb)
    assert eb.tripped_reason("ma_trend") == before       # unchanged -> skill was skipped, not run


def test_intermittent_failures_do_not_trip():
    led = MemoryLedger("ma_trend", 5000.0)
    eb = ErrorBoundary(threshold=3)

    class Flaky(Strategy):
        name = "ma_trend"
        def __init__(self):
            self.calls = 0
        def on_candle(self, candles, position):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient")
            return Signal("HOLD")
    flaky = Flaky()
    _run(led, [flaky], eb)                               # crash #1
    _run(led, [flaky], eb)                               # success -> streak reset
    _run(led, [flaky], eb)                               # success
    assert not eb.is_tripped("ma_trend") and eb.consecutive_failures("ma_trend") == 0
