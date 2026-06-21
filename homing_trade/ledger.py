from homing_trade.models import Position


class MemoryLedger:
    """In-memory stand-in for Database, matching the method surface that
    engine.process_tick and its helpers use. Lets the backtester run through
    the exact live execution path without touching SQLite."""

    def __init__(self, strategy: str, starting_balance: float):
        self.strategy = strategy
        self._balance = {strategy: starting_balance}
        self._open: dict[str, Position] = {}
        self._next_id = 1
        self.trades: list[dict] = []
        self.equity_curve: list[tuple[int, float]] = []

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

    def record_trade(self, strategy, position_id, side, action, price, size, fee, pnl, ts):
        self.trades.append({"strategy": strategy, "position_id": position_id, "side": side,
                            "action": action, "price": price, "size": size, "fee": fee,
                            "pnl": pnl, "ts": ts})

    def record_equity(self, strategy, equity, ts):
        self.equity_curve.append((ts, equity))

    def log_decision(self, *args, **kwargs):
        pass

    def recent_close_pnls(self, strategy, limit):
        closes = [t["pnl"] for t in self.trades
                  if t["strategy"] == strategy and t["action"] == "CLOSE"]
        return list(reversed(closes))[:limit]
