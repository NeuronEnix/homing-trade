from algotrading.skills.base import Strategy
from algotrading.models import Signal
from algotrading.agents.heuristic import BullAgent, BearAgent, RiskSupervisor


def build_agents(mode, cfg):
    if mode == "llm":
        from algotrading.agents.llm import LlmAgent
        return (LlmAgent("bull", cfg.llm_model),
                LlmAgent("bear", cfg.llm_model),
                LlmAgent("risk", cfg.llm_model))
    return (BullAgent(cfg.rl_fast, cfg.rl_slow),
            BearAgent(cfg.rl_fast, cfg.rl_slow),
            RiskSupervisor(cfg.risk_vol_window, cfg.risk_vol_threshold))


class Committee(Strategy):
    name = "committee"

    def __init__(self, agents=None, threshold: float = 0.2):
        self.threshold = threshold
        self.bull, self.bear, self.risk = agents if agents else (BullAgent(), BearAgent(), RiskSupervisor())

    def on_candle(self, candles, position):
        bull = self.bull.assess(candles, position)
        bear = self.bear.assess(candles, position)
        risk = self.risk.assess(candles, position)
        is_long = position is not None and position.side == "LONG"
        ind = {"bull": bull.stance, "bear": bear.stance, "risk": risk.stance}
        if risk.stance == "BEARISH":
            action = "CLOSE" if is_long else "HOLD"
            return Signal(action=action, confidence=risk.confidence,
                          reason=f"risk veto: {risk.reason}", indicators=ind)
        net = (bull.confidence if bull.stance == "BULLISH" else 0.0) - \
              (bear.confidence if bear.stance == "BEARISH" else 0.0)
        ind["net"] = round(net, 3)
        if net > self.threshold and not is_long:
            return Signal("LONG", confidence=min(1.0, net), reason=f"bull consensus: {bull.reason}", indicators=ind)
        if net < -self.threshold and is_long:
            return Signal("CLOSE", confidence=min(1.0, -net), reason=f"bear consensus: {bear.reason}", indicators=ind)
        return Signal("HOLD", reason="no consensus", indicators=ind)
