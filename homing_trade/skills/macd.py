from homing_trade.skills.base import Strategy
from homing_trade.skills.indicators import macd
from homing_trade.models import Candle, Position, Signal


class MacdCross(Strategy):
    """MACD line crossing its signal line. LONG on a bullish cross (macd crosses above
    signal), CLOSE on a bearish cross. A classic trend-timing strategy."""

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
        is_long = position is not None and position.side == "LONG"
        if m_prev <= s_prev and m_now > s_now and not is_long:
            return Signal("LONG", confidence=0.6, reason="MACD crossed above signal", indicators=ind)
        if m_prev >= s_prev and m_now < s_now and is_long:
            return Signal("CLOSE", confidence=0.6, reason="MACD crossed below signal", indicators=ind)
        return Signal("HOLD", reason="no cross", indicators=ind)
