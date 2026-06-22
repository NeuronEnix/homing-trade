from homing_trade.models import Position
from homing_trade.ledger_base import Ledger


class MemoryLedger(Ledger):
    """In-memory Ledger backend, used by the backtester. Lets the backtester run
    through the exact live execution path (engine.process_tick) without touching
    SQLite. Implements the Ledger interface defined in ledger_base."""

    def __init__(self, strategy: str, starting_balance: float):
        self.strategy = strategy
        self._balance = {strategy: starting_balance}
        self._open: dict[str, Position] = {}
        self._next_id = 1
        self.trades: list[dict] = []
        self.equity_curve: list[tuple[int, float]] = []
        self.risk_events: list[dict] = []
        self.regimes: list[dict] = []

    def ensure_strategy(self, name, starting_balance):
        self._balance.setdefault(name, starting_balance)

    def get_balance(self, name):
        return self._balance.get(name, 0.0)

    def set_balance(self, name, balance):
        self._balance[name] = balance

    def open_position(self, pos: Position) -> int:
        pos.id = self._next_id
        pos.status = "open"
        self._open[pos.strategy] = pos
        self._next_id += 1
        return pos.id

    def close_position(self, position_id):
        for name, pos in list(self._open.items()):
            if pos.id == position_id:
                del self._open[name]
                return

    def get_open_position(self, name):
        return self._open.get(name)

    def record_trade(self, strategy, position_id, side, action, price, size, fee, pnl, ts,
                     *, decision_price=None, slippage=None, exit_reason=None):
        self.trades.append({"strategy": strategy, "position_id": position_id, "side": side,
                            "action": action, "price": price, "size": size, "fee": fee,
                            "pnl": pnl, "ts": ts, "decision_price": decision_price,
                            "slippage": slippage, "exit_reason": exit_reason})

    def record_equity(self, strategy, equity, ts):
        self.equity_curve.append((ts, equity))

    def record_risk_event(self, ts, strategy, kind, reason, notional=None):
        self.risk_events.append({"ts": ts, "strategy": strategy, "kind": kind,
                                 "reason": reason, "notional": notional})

    def record_regime(self, pair, interval, time, regime, adx=None, ema_slope=None, realized_vol=None):
        self.regimes.append({"pair": pair, "interval": interval, "time": time, "regime": regime,
                             "adx": adx, "ema_slope": ema_slope, "realized_vol": realized_vol})

    def log_decision(self, *args, **kwargs):
        pass

    def record_llm_response(self, *args, **kwargs):
        pass

    def latest_llm_rationale(self, strategy):
        return ""

    def recent_close_pnls(self, strategy, limit):
        closes = [t["pnl"] for t in self.trades
                  if t["strategy"] == strategy and t["action"] == "CLOSE"]
        return list(reversed(closes))[:limit]
