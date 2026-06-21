from algotrading.skills.base import Strategy
from algotrading.skills.indicators import ema
from algotrading.models import Candle, Position, Signal


class MaTrend(Strategy):
    name = "ma_trend"

    def __init__(self, fast: int = 9, slow: int = 21):
        self.fast = fast
        self.slow = slow

    def on_candle(self, candles: list[Candle], position: Position | None) -> Signal:
        closes = [c.close for c in candles]
        if len(closes) < self.slow + 1:
            return Signal(action="HOLD", reason="warming up")
        fast_now = ema(closes, self.fast)
        slow_now = ema(closes, self.slow)
        fast_prev = ema(closes[:-1], self.fast)
        slow_prev = ema(closes[:-1], self.slow)
        ind = {"fast": round(fast_now, 4), "slow": round(slow_now, 4)}
        if fast_prev <= slow_prev and fast_now > slow_now:
            return Signal(action="LONG", confidence=0.6,
                          reason=f"EMA{self.fast} crossed above EMA{self.slow}", indicators=ind)
        if fast_prev >= slow_prev and fast_now < slow_now:
            return Signal(action="SHORT", confidence=0.6,
                          reason=f"EMA{self.fast} crossed below EMA{self.slow}", indicators=ind)
        return Signal(action="HOLD", reason="no crossover", indicators=ind)
