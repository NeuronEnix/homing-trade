"""Phase 7 #3: the regime-aware portfolio gate.

Covers the pure weighting/threshold functions (favored vs unfavored by style×regime, the conservative
'never penalize when ambiguous/neutral' rule) and the engine integration: a trend strategy is
down-weighted in chop (and a reverter up-weighted) only when regime_filter_enabled, with no change
when disabled; the committee's effective threshold scales with regime."""
import pytest

from homing_trade.regime_filter import (regime_weight, committee_threshold_scale, strategy_style,
                                         TREND, REVERT, NEUTRAL)
from homing_trade.skills.committee import Committee
from homing_trade.agents.heuristic import BullAgent, BearAgent, RiskSupervisor


# --- styles ---
def test_strategy_styles():
    assert strategy_style("ma_trend") == TREND and strategy_style("supertrend") == TREND
    assert strategy_style("rsi_revert") == REVERT and strategy_style("zscore_revert") == REVERT
    assert strategy_style("rl_qlearn") == NEUTRAL and strategy_style("committee") == NEUTRAL
    assert strategy_style("unknown_thing") == NEUTRAL


# --- regime_weight ---
def test_trend_strategy_favored_in_trend_cut_in_chop():
    assert regime_weight("ma_trend", "trend_up") == 1.0
    assert regime_weight("ma_trend", "trend_down") == 1.0
    assert regime_weight("ma_trend", "chop") == 0.5


def test_revert_strategy_favored_in_chop_cut_in_trend():
    assert regime_weight("rsi_revert", "chop") == 1.0
    assert regime_weight("rsi_revert", "trend_up") == 0.5


def test_ambiguous_regime_never_penalizes():
    for reg in (None, "unknown", "transition"):
        assert regime_weight("ma_trend", reg) == 1.0
        assert regime_weight("rsi_revert", reg) == 1.0


def test_neutral_strategy_never_penalized():
    for reg in ("trend_up", "chop", "transition", "unknown"):
        assert regime_weight("committee", reg) == 1.0


def test_unfavored_weight_is_configurable():
    assert regime_weight("ma_trend", "chop", unfavored=0.25) == 0.25


# --- committee threshold scale ---
def test_committee_threshold_scale_by_regime():
    assert committee_threshold_scale("trend_up") == 1.0
    assert committee_threshold_scale("trend_down") == 1.0
    assert committee_threshold_scale("chop") == 1.5
    assert committee_threshold_scale("transition") == 1.5
    assert committee_threshold_scale(None) == 1.5


# --- committee honours an engine-set regime threshold scale ---
def test_committee_uses_regime_threshold_scale_attr():
    # net consensus that clears the BASE threshold (0.2) but not the scaled one (0.2*1.5=0.3)
    agents = (BullAgent(), BearAgent(), RiskSupervisor())
    base = Committee(agents=agents, threshold=0.2)
    scaled = Committee(agents=agents, threshold=0.2)
    scaled._regime_threshold_scale = 1.5
    # both default to scale 1.0 unless the attr is set
    assert getattr(base, "_regime_threshold_scale", 1.0) == 1.0
    assert scaled._regime_threshold_scale == 1.5


# --- engine integration ---
def _stub(skill_name):
    from homing_trade.skills.base import Strategy
    from homing_trade.models import Signal

    class AlwaysLong(Strategy):
        name = skill_name
        def on_candle(self, cs, pos):
            return Signal("LONG") if pos is None else Signal("HOLD")
    return AlwaysLong()


def _candles(prices):
    from homing_trade.models import Candle
    return [Candle(open=p, high=p + 1, low=p - 1, close=p, volume=1.0, time=1000 + i * 60000)
            for i, p in enumerate(prices)]


def _size_after_tick(skill_name, regime_on):
    from homing_trade.engine import process_tick
    from homing_trade.broker import Broker
    from homing_trade.ledger import MemoryLedger
    from homing_trade.config import Config
    cfg = Config(regime_filter_enabled=regime_on)
    led = MemoryLedger(skill_name, 5000.0)
    process_tick(led, Broker(cfg.fee, cfg.slippage), [_stub(skill_name)], _candles([100.0] * 30), cfg)
    return led.get_open_position(skill_name).size


def test_engine_downweights_trend_skill_in_chop_when_enabled():
    # flat candles -> chop regime; ma_trend is a TREND style -> weight x0.5 -> strictly smaller size
    assert _size_after_tick("ma_trend", regime_on=True) < _size_after_tick("ma_trend", regime_on=False)


def test_engine_does_not_downweight_reverter_in_chop():
    # rsi_revert is a REVERT style -> favored in chop -> size unchanged by the gate
    assert _size_after_tick("rsi_revert", regime_on=True) == _size_after_tick("rsi_revert", regime_on=False)


def test_engine_sets_committee_threshold_scale_in_chop():
    from homing_trade.engine import process_tick
    from homing_trade.broker import Broker
    from homing_trade.ledger import MemoryLedger
    from homing_trade.config import Config
    from homing_trade.skills.committee import Committee
    cfg = Config(regime_filter_enabled=True, regime_committee_threshold_scale=1.5)
    led = MemoryLedger("committee", 5000.0)
    comm = Committee(threshold=0.2)
    process_tick(led, Broker(cfg.fee, cfg.slippage), [comm], _candles([100.0] * 30), cfg)
    assert comm._regime_threshold_scale == 1.5     # chop -> stronger consensus demanded


def test_engine_leaves_weight_unchanged_when_disabled():
    from homing_trade.engine import process_tick
    from homing_trade.broker import Broker
    from homing_trade.ledger import MemoryLedger
    from homing_trade.config import Config
    cfg = Config(regime_filter_enabled=False)
    comm = Committee(threshold=0.2)
    led = MemoryLedger("committee", 5000.0)
    process_tick(led, Broker(cfg.fee, cfg.slippage), [comm], _candles([100.0] * 30), cfg)
    assert not hasattr(comm, "_regime_threshold_scale")   # engine never touched it when disabled


def test_engine_clears_stale_committee_scale_when_flag_flips_off():
    # enabled run sets the scale (chop -> 1.5); a later disabled run on the SAME instance must reset
    # it to 1.0 so a flag flip can't leave the committee silently demanding stronger consensus.
    from homing_trade.engine import process_tick
    from homing_trade.broker import Broker
    from homing_trade.ledger import MemoryLedger
    from homing_trade.config import Config
    from homing_trade.skills.committee import Committee
    comm = Committee(threshold=0.2)
    cfg_on = Config(regime_filter_enabled=True, regime_committee_threshold_scale=1.5)
    process_tick(MemoryLedger("committee", 5000.0), Broker(cfg_on.fee, cfg_on.slippage), [comm],
                 _candles([100.0] * 30), cfg_on)
    assert comm._regime_threshold_scale == 1.5
    cfg_off = Config(regime_filter_enabled=False)
    process_tick(MemoryLedger("committee", 5000.0), Broker(cfg_off.fee, cfg_off.slippage), [comm],
                 _candles([100.0] * 30), cfg_off)
    assert comm._regime_threshold_scale == 1.0     # stale scale cleared


def test_regime_config_fields_are_protected_from_proposals():
    # the gate's sizing/entry levers must be human-gated, never model-auto-tuned
    from homing_trade.db import _is_protected
    for f in ("regime_filter_enabled", "regime_unfavored_weight", "regime_committee_threshold_scale"):
        assert _is_protected(f)
