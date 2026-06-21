from homing_trade.skills.committee import Committee, build_agents
from homing_trade.agents.base import Agent, AgentView
from homing_trade.agents.heuristic import BullAgent, BearAgent, RiskSupervisor
from homing_trade.config import CONFIG
from homing_trade.models import Candle, Position


def candles_from(prices):
    return [Candle(open=p, high=p + 1, low=p - 1, close=p, volume=1.0, time=1000 + i * 60000)
            for i, p in enumerate(prices)]


class _Stub(Agent):
    def __init__(self, view):
        self._view = view
    def assess(self, candles, position):
        return self._view


def long_pos():
    return Position(strategy="committee", side="LONG", entry_price=100, size=1,
                    leverage=3, margin=33, stop_price=98, opened_at=0)


def test_build_agents_heuristic_default():
    bull, bear, risk = build_agents("heuristic", CONFIG)
    assert isinstance(bull, BullAgent) and isinstance(bear, BearAgent) and isinstance(risk, RiskSupervisor)


def test_risk_veto_closes_long():
    c = Committee(agents=(_Stub(AgentView("BULLISH", 0.9, "x")),
                          _Stub(AgentView("NEUTRAL", 0.0, "x")),
                          _Stub(AgentView("BEARISH", 0.8, "veto"))))
    assert c.on_candle(candles_from([100.0] * 30), long_pos()).action == "CLOSE"


def test_bull_dominant_opens_long():
    c = Committee(agents=(_Stub(AgentView("BULLISH", 0.9, "x")),
                          _Stub(AgentView("NEUTRAL", 0.0, "x")),
                          _Stub(AgentView("NEUTRAL", 0.0, "ok"))), threshold=0.2)
    assert c.on_candle(candles_from([100.0] * 30), None).action == "LONG"


def test_bear_dominant_closes_long():
    c = Committee(agents=(_Stub(AgentView("NEUTRAL", 0.0, "x")),
                          _Stub(AgentView("BEARISH", 0.9, "x")),
                          _Stub(AgentView("NEUTRAL", 0.0, "ok"))), threshold=0.2)
    assert c.on_candle(candles_from([100.0] * 30), long_pos()).action == "CLOSE"


def test_no_consensus_holds():
    c = Committee(agents=(_Stub(AgentView("NEUTRAL", 0.0, "x")),
                          _Stub(AgentView("NEUTRAL", 0.0, "x")),
                          _Stub(AgentView("NEUTRAL", 0.0, "ok"))))
    assert c.on_candle(candles_from([100.0] * 30), None).action == "HOLD"
