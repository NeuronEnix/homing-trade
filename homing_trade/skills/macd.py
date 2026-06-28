from homing_trade.skills.base import Strategy
from homing_trade.skills.indicators import macd
from homing_trade.models import Candle, Position, Signal


class MacdCross(Strategy):
    """MACD line crossing its signal line. Symmetric trend-timing on a futures instrument:
    LONG on a bullish cross (macd crosses above signal), SHORT on a bearish cross. The engine
    closes-and-flips when the cross opposes an open position, so a held long is reversed into a
    short on a bearish cross (and vice-versa) rather than just sitting to the stop."""

    name = "macd"

    def __init__(self, fast=12, slow=26, signal=9):
        self.fast = fast
        self.slow = slow
        self.signal = signal

    def on_candle(self, candles, position):
        closes = [c.close for c in candles]
        m_now, s_now = macd(closes, self.fast, self.slow, self.signal)
        m_prev, s_prev = macd(closes[:-1], self.fast, self.slow, self.signal)
        if None in (m_now, s_now, m_prev, s_prev):
            return Signal("HOLD", reason="warming up")
        ind = {"macd": round(m_now, 2), "signal": round(s_now, 2)}
        if m_prev <= s_prev and m_now > s_now:
            return Signal("LONG", confidence=0.6, reason="MACD crossed above signal", indicators=ind)
        if m_prev >= s_prev and m_now < s_now:
            return Signal("SHORT", confidence=0.6, reason="MACD crossed below signal", indicators=ind)
        return Signal("HOLD", reason="no cross", indicators=ind)
