from algotrading.agents.base import Agent, AgentView
from algotrading.agents.heuristic import BullAgent, BearAgent, RiskSupervisor
from algotrading.models import Candle


def candles_from(prices, span=1.0):
    return [Candle(open=p, high=p + span, low=p - span, close=p, volume=1.0, time=1000 + i * 60000)
            for i, p in enumerate(prices)]


def test_agentview_fields():
    v = AgentView("BULLISH", 0.7, "x")
    assert v.stance == "BULLISH" and v.confidence == 0.7


def test_bull_bullish_on_uptrend():
    v = BullAgent().assess(candles_from([float(x) for x in range(1, 60)]), None)
    assert v.stance == "BULLISH"


def test_bear_bearish_on_downtrend():
    v = BearAgent().assess(candles_from([float(x) for x in range(60, 1, -1)]), None)
    assert v.stance == "BEARISH"


def test_bull_warming_up_neutral():
    assert BullAgent().assess(candles_from([1.0, 2.0]), None).stance == "NEUTRAL"


def test_risk_veto_on_high_volatility():
    # flat price but huge candle ranges -> high volatility -> veto
    prices = [100.0] * 30
    candles = candles_from(prices, span=20.0)  # range ~40 vs ref 100 -> 0.4 > 0.04
    assert RiskSupervisor(window=20, vol_threshold=0.04).assess(candles, None).stance == "BEARISH"


def test_risk_neutral_on_calm():
    prices = [100.0] * 30
    candles = candles_from(prices, span=0.1)
    assert RiskSupervisor(window=20, vol_threshold=0.04).assess(candles, None).stance == "NEUTRAL"
