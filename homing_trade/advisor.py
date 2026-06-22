"""Position sizing / entry planning.

Separates execution policy — how much to trade, where the stop sits, what leverage — from
the strategy's decision (action + confidence) and from the bookkeeping (PositionManager).
A strategy returns only a Signal; it never reaches into cfg for sizing. The Advisor owns
that policy, using the Broker for the actual size/stop math.
"""
from dataclasses import dataclass

from homing_trade.config import effective_leverage


@dataclass
class EntryPlan:
    size: float
    margin: float
    stop_price: float
    leverage: float


class Advisor:
    def __init__(self, cfg, broker):
        self.cfg = cfg
        self.broker = broker

    def plan_entry(self, balance, entry_fill, side, weight=1.0) -> EntryPlan:
        """Size an entry from the available balance and the configured risk policy.

        weight (0..1) scales the per-trade risk fraction, e.g. for allocator weighting.
        """
        lev = effective_leverage(self.cfg)
        size, margin = self.broker.position_size(
            balance, entry_fill, self.cfg.risk_pct * weight, self.cfg.stop_pct, lev)
        stop = self.broker.stop_price(entry_fill, side, self.cfg.stop_pct)
        return EntryPlan(size=size, margin=margin, stop_price=stop, leverage=lev)
