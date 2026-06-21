from homing_trade.agents.base import Agent, AgentView
from homing_trade.skills.indicators import ema, rsi


class BullAgent(Agent):
    name = "bull"

    def __init__(self, fast: int = 9, slow: int = 21):
        self.fast = fast
        self.slow = slow

    def assess(self, candles, position):
        closes = [c.close for c in candles]
        f, s, r = ema(closes, self.fast), ema(closes, self.slow), rsi(closes, 14)
        if f is None or s is None or r is None:
            return AgentView("NEUTRAL", 0.0, "warming up")
        if f > s and r > 30:
            conf = max(0.3, min(1.0, (f - s) / s * 50))
            return AgentView("BULLISH", conf, f"uptrend EMA{self.fast}>EMA{self.slow}, RSI {r:.0f}")
        return AgentView("NEUTRAL", 0.1, "no bullish edge")


class BearAgent(Agent):
    name = "bear"

    def __init__(self, fast: int = 9, slow: int = 21):
        self.fast = fast
        self.slow = slow

    def assess(self, candles, position):
        closes = [c.close for c in candles]
        f, s, r = ema(closes, self.fast), ema(closes, self.slow), rsi(closes, 14)
        if f is None or s is None or r is None:
            return AgentView("NEUTRAL", 0.0, "warming up")
        if f < s and r < 70:
            conf = max(0.3, min(1.0, (s - f) / s * 50))
            return AgentView("BEARISH", conf, f"downtrend EMA{self.fast}<EMA{self.slow}, RSI {r:.0f}")
        return AgentView("NEUTRAL", 0.1, "no bearish edge")


class RiskSupervisor(Agent):
    name = "risk"

    def __init__(self, window: int = 20, vol_threshold: float = 0.04):
        self.window = window
        self.vol_threshold = vol_threshold

    def assess(self, candles, position):
        if len(candles) < self.window:
            return AgentView("NEUTRAL", 0.0, "warming up")
        window = candles[-self.window:]
        ref = sum(c.close for c in window) / len(window)
        vol = (max(c.high for c in window) - min(c.low for c in window)) / ref if ref else 0.0
        if vol > self.vol_threshold:
            conf = min(1.0, vol / self.vol_threshold - 1 + 0.5)
            return AgentView("BEARISH", conf, f"high volatility {vol:.2%} > {self.vol_threshold:.2%} — veto")
        return AgentView("NEUTRAL", 0.2, f"volatility {vol:.2%} acceptable")
