"""Repository: the SQLite-backed Ledger — a typed domain API over `Database`.

Application code (engine, report, web, backtest) should depend on Repository, not on
raw SQL or on `Database` internals. `Database` stays the low-level SQL/schema/migration
layer; Repository is the typed surface the rest of the app talks to, and the SQLite
implementation of the `Ledger` interface (so it is interchangeable with MemoryLedger).
"""
from homing_trade.db import Database
from homing_trade.ledger_base import Ledger
from homing_trade.models import Position


class Repository(Ledger):
    def __init__(self, db: Database):
        self.db = db

    @classmethod
    def open(cls, path: str) -> "Repository":
        """Open (creating + migrating if needed) a SQLite-backed repository at `path`."""
        return cls(Database(path))

    # --- Ledger interface: delegated to the underlying Database ---
    def ensure_strategy(self, name, starting_balance):
        return self.db.ensure_strategy(name, starting_balance)

    def get_balance(self, name):
        return self.db.get_balance(name)

    def set_balance(self, name, balance):
        return self.db.set_balance(name, balance)

    def open_position(self, pos: Position) -> int:
        return self.db.open_position(pos)

    def close_position(self, position_id):
        return self.db.close_position(position_id)

    def get_open_position(self, name):
        return self.db.get_open_position(name)

    def record_trade(self, strategy, position_id, side, action, price, size, fee, pnl, ts):
        return self.db.record_trade(strategy, position_id, side, action, price, size, fee, pnl, ts)

    def record_equity(self, strategy, equity, ts):
        return self.db.record_equity(strategy, equity, ts)

    def log_decision(self, strategy, ts, candle_time, action, confidence, reason, indicators):
        return self.db.log_decision(strategy, ts, candle_time, action, confidence, reason, indicators)

    def record_llm_response(self, strategy, ts, backend, model, action, confidence,
                            observation, prediction, rationale, raw, error):
        return self.db.record_llm_response(strategy, ts, backend, model, action, confidence,
                                           observation, prediction, rationale, raw, error)

    def latest_llm_rationale(self, strategy):
        return self.db.latest_llm_rationale(strategy)

    def recent_close_pnls(self, strategy, limit):
        return self.db.recent_close_pnls(strategy, limit)

    # --- typed read methods (these absorb the raw SQL that lived in report.py) ---
    def closed_pnls(self, strategy):
        """All realized PnLs for CLOSE trades, oldest-first."""
        return self.db.closed_pnls(strategy)

    def equity_series(self, strategy):
        """Equity snapshots for a strategy, oldest-first."""
        return self.db.equity_series(strategy)

    # --- live-loop methods used by engine.run (beyond the minimal Ledger interface) ---
    def max_trade_id(self) -> int:
        return self.db.max_trade_id()

    def save_candles(self, pair, interval, candles, source) -> int:
        return self.db.save_candles(pair, interval, candles, source)

    def get_state(self, key) -> str | None:
        return self.db.get_state(key)

    def set_state(self, key, value) -> None:
        return self.db.set_state(key, value)

    def trades_after(self, last_id) -> list:
        return self.db.trades_after(last_id)

    def get_candles_range(self, pair, interval, start_ms, end_ms, source="all"):
        return self.db.get_candles_range(pair, interval, start_ms, end_ms, source=source)

    def get_candle_bounds(self, pair, interval):
        return self.db.get_candle_bounds(pair, interval)

    def close(self):
        return self.db.close()
