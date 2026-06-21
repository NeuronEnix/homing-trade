"""Daily risk controls — kept separate from execution (engine) and signals (skills).

`DailyRiskGuard` is the single place that decides whether a new position may open:
  - a master on/off switch (`trading_enabled`) to stop trading immediately,
  - a per-day notional cap (`max_trade_amount_per_day`), and
  - a daily-loss KILL SWITCH (`max_daily_loss`) that halts trading for the rest of the
    day once realized losses breach the limit.

All limits default to "off" (0 / enabled), so with no config the guard never blocks —
behaviour is identical to having no guard at all. The "day" is derived from the candle
timestamp, so it works the same in live paper trading and in backtests.
"""
_DAY_MS = 86_400_000


class DailyRiskGuard:
    def __init__(self, max_trade_amount_per_day=0.0, max_daily_loss=0.0, enabled=True):
        self.max_trade_amount_per_day = max_trade_amount_per_day  # 0 = no cap
        self.max_daily_loss = max_daily_loss                      # 0 = no kill switch
        self.enabled = enabled                                    # master switch
        self._day = None
        self.traded_today = 0.0       # notional opened today
        self.realized_today = 0.0     # realized pnl today (negative = a loss)
        self.halted_reason = None     # set when the kill switch / cap blocks trading

    @classmethod
    def from_config(cls, cfg):
        return cls(
            max_trade_amount_per_day=getattr(cfg, "max_trade_amount_per_day", 0.0),
            max_daily_loss=getattr(cfg, "max_daily_loss", 0.0),
            enabled=getattr(cfg, "trading_enabled", True),
        )

    def _roll(self, ts_ms):
        day = ts_ms // _DAY_MS
        if day != self._day:
            self._day = day
            self.traded_today = 0.0
            self.realized_today = 0.0
            self.halted_reason = None  # a new day clears the daily kill switch

    def _check_loss(self):
        if self.max_daily_loss > 0 and -self.realized_today >= self.max_daily_loss:
            self.halted_reason = (f"kill switch: daily loss ₹{-self.realized_today:.2f} "
                                  f"reached limit ₹{self.max_daily_loss:.2f}")

    def can_open(self, notional, ts_ms):
        """Return (allowed: bool, reason: str). Read-only — does not record anything."""
        self._roll(ts_ms)
        if not self.enabled:
            return False, "trading disabled (master switch)"
        self._check_loss()
        if self.halted_reason:
            return False, self.halted_reason
        if (self.max_trade_amount_per_day > 0
                and self.traded_today + notional > self.max_trade_amount_per_day):
            return False, (f"daily trade cap: ₹{self.traded_today:.0f}+₹{notional:.0f} "
                           f"> ₹{self.max_trade_amount_per_day:.0f}")
        return True, ""

    def record_open(self, notional, ts_ms):
        self._roll(ts_ms)
        self.traded_today += notional

    def record_close(self, pnl, ts_ms):
        self._roll(ts_ms)
        self.realized_today += pnl
        self._check_loss()
