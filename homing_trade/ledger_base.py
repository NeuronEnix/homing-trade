"""The Ledger interface: the execution-and-accounting surface that the engine
depends on, independent of any storage backend.

Two backends implement it:
  - `ledger.MemoryLedger`  — in-memory, used by the backtester (fast, disposable).
  - `repository.Repository` — SQLite-backed, used for live/paper trading.

`engine.process_tick` (and its open/close helpers) operate against this interface
only, so the same execution path runs in backtests and live without change. This
replaces the previous duck-typed arrangement where both classes merely happened to
share method names.
"""
from abc import ABC, abstractmethod

from homing_trade.models import Position


class Ledger(ABC):
    @abstractmethod
    def ensure_strategy(self, name: str, starting_balance: float) -> None: ...

    @abstractmethod
    def get_balance(self, name: str) -> float: ...

    @abstractmethod
    def set_balance(self, name: str, balance: float) -> None: ...

    @abstractmethod
    def open_position(self, pos: Position) -> int: ...

    @abstractmethod
    def close_position(self, position_id: int) -> None: ...

    @abstractmethod
    def get_open_position(self, name: str) -> Position | None: ...

    @abstractmethod
    def record_trade(self, strategy, position_id, side, action, price, size, fee, pnl, ts) -> None: ...

    @abstractmethod
    def record_equity(self, strategy, equity, ts) -> None: ...

    @abstractmethod
    def log_decision(self, strategy, ts, candle_time, action, confidence, reason, indicators) -> None: ...

    @abstractmethod
    def record_llm_response(self, strategy, ts, backend, model, action, confidence,
                            observation, prediction, rationale, raw, error) -> None: ...

    @abstractmethod
    def latest_llm_rationale(self, strategy) -> str: ...

    @abstractmethod
    def recent_close_pnls(self, strategy, limit): ...
