"""PositionManager — acts on a strategy's decision against a Ledger.

Owns the execution mechanics that used to be inline in engine.process_tick: opening and
closing positions (balance/fee bookkeeping + the daily-risk guard), and the stop/liquidation
risk checks on an open position. Sizing is delegated to an Advisor, market math to the
Broker, persistence to the Ledger. engine.process_tick orchestrates strategies and calls
into this; the per-action plumbing lives here.
"""
from homing_trade.advisor import Advisor
from homing_trade.models import Position


class PositionManager:
    def __init__(self, ledger, broker, cfg=None, guard=None, advisor=None):
        self.ledger = ledger
        self.broker = broker
        self.cfg = cfg
        self.guard = guard
        self._advisor = advisor

    @property
    def advisor(self):
        # Built lazily so close()/manage_risk() work without a cfg (they need no sizing).
        if self._advisor is None:
            self._advisor = Advisor(self.cfg, self.broker)
        return self._advisor

    def manage_risk(self, skill, position, candle, now_ms):
        """Close on liquidation or stop-out; return the surviving position (or None)."""
        if position is None:
            return None
        if self.broker.hit_liquidation(position, candle):
            self.close(skill, position, self.broker.liquidation_price(position), candle, now_ms)
            return None
        if self.broker.hit_stop(position, candle):
            self.close(skill, position, position.stop_price, candle, now_ms)
            return None
        return position

    def open(self, skill, side, candle, now_ms, weight=1.0):
        """Open a position sized by the Advisor.

        Returns (opened, reason): (True, None) on success, or (False, reason) when the
        risk guard blocks it — in which case a 'veto' risk_event is also recorded.
        """
        balance = self.ledger.get_balance(skill.name)
        entry_fill = self.broker.fill_price(candle.close, side, is_entry=True)
        plan = self.advisor.plan_entry(balance, entry_fill, side, weight)
        notional = plan.size * entry_fill
        if self.guard is not None:
            ok, reason = self.guard.can_open(notional, now_ms)
            if not ok:
                self.ledger.record_risk_event(now_ms, skill.name, "veto", reason, notional)
                return (False, reason)  # blocked by daily risk limits / kill switch
            self.guard.record_open(notional, now_ms)
        fee = self.broker.entry_fee(plan.size, entry_fill)
        self.ledger.set_balance(skill.name, balance - fee)
        pos = Position(strategy=skill.name, side=side, entry_price=entry_fill, size=plan.size,
                       leverage=plan.leverage, margin=plan.margin, stop_price=plan.stop_price,
                       opened_at=candle.time)
        pid = self.ledger.open_position(pos)
        self.ledger.record_trade(skill.name, pid, side, "OPEN", entry_fill, plan.size, fee, -fee, now_ms,
                                 decision_price=candle.close, slippage=entry_fill - candle.close)
        return (True, None)

    def close(self, skill, position, exit_price, candle, now_ms):
        """Close a position at exit_price; books pnl/fee and records the trade."""
        exit_fill = self.broker.fill_price(exit_price, position.side, is_entry=False)
        pnl = self.broker.realized_pnl(position, exit_fill)
        fee = self.broker.entry_fee(position.size, exit_fill)
        balance = self.ledger.get_balance(skill.name) + pnl - fee
        self.ledger.set_balance(skill.name, balance)
        self.ledger.close_position(position.id)
        self.ledger.record_trade(skill.name, position.id, position.side, "CLOSE", exit_fill,
                                 position.size, fee, pnl - fee, now_ms,
                                 decision_price=exit_price, slippage=exit_fill - exit_price)
        if self.guard is not None:
            self.guard.record_close(pnl - fee, now_ms)
        return balance
