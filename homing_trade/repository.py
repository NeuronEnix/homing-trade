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

    def record_trade(self, *args, **kwargs):
        # Passthrough so decision_price/slippage kwargs reach Database.
        return self.db.record_trade(*args, **kwargs)

    def record_equity(self, strategy, equity, ts):
        return self.db.record_equity(strategy, equity, ts)

    def log_decision(self, *args, **kwargs):
        # Passthrough so the provenance kwargs (decision_id, intended/taken_action, …) flow to Database.
        return self.db.log_decision(*args, **kwargs)

    def record_llm_response(self, *args, **kwargs):
        # Passthrough so the replay kwargs (next_check_in_sec, requested_charts, …) reach Database.
        return self.db.record_llm_response(*args, **kwargs)

    def latest_llm_rationale(self, strategy):
        return self.db.latest_llm_rationale(strategy)

    def record_cost(self, strategy, ts, model, backend, prompt_tokens, completion_tokens, usd):
        return self.db.record_cost(strategy, ts, model, backend, prompt_tokens, completion_tokens, usd)

    def cost_summary(self, strategy=None):
        return self.db.cost_summary(strategy)

    # --- Phase-6 external-signal cache ---
    def upsert_signal(self, source, key, ts, value, fetched_at):
        return self.db.upsert_signal(source, key, ts, value, fetched_at)

    def get_signal(self, source, key):
        return self.db.get_signal(source, key)

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

    # --- dashboard reads + admin (used by web.py) ---
    def strategy_names(self) -> list:
        return self.db.strategy_names()

    def latest_equity(self, strategy):
        return self.db.latest_equity(strategy)

    def recent_trades(self, limit) -> list:
        return self.db.recent_trades(limit)

    def recent_decisions(self, limit) -> list:
        return self.db.recent_decisions(limit)

    def taken_action_counts(self, strategy) -> dict:
        return self.db.taken_action_counts(strategy)

    def recent_llm_responses(self, strategy=None, limit=20):
        return self.db.recent_llm_responses(strategy, limit)

    def reset_paper_ledger(self) -> None:
        return self.db.reset_paper_ledger()

    def record_risk_event(self, ts, strategy, kind, reason, notional=None):
        return self.db.record_risk_event(ts, strategy, kind, reason, notional)

    def recent_risk_events(self, limit=50):
        return self.db.recent_risk_events(limit)

    def record_regime(self, pair, interval, time, regime, adx=None, ema_slope=None, realized_vol=None):
        return self.db.record_regime(pair, interval, time, regime, adx, ema_slope, realized_vol)

    def latest_regime(self, pair, interval):
        return self.db.latest_regime(pair, interval)

    def rebuild_trade_outcomes(self, pair=None, interval=None):
        return self.db.rebuild_trade_outcomes(pair, interval)

    def trade_outcomes(self, strategy=None, as_of=None) -> list:
        return self.db.trade_outcomes(strategy, as_of)

    def outcomes_with_confidence(self, strategy=None, as_of=None) -> list:
        return self.db.outcomes_with_confidence(strategy, as_of)

    def outcomes_with_playbook(self, strategy=None, as_of=None) -> list:
        return self.db.outcomes_with_playbook(strategy, as_of)

    # --- Phase-4 reflections + playbooks ---
    def record_reflection(self, *args, **kwargs):
        return self.db.record_reflection(*args, **kwargs)

    def recent_reflections(self, strategy=None, limit=50, kind=None):
        return self.db.recent_reflections(strategy, limit, kind)

    def get_decision(self, decision_id):
        return self.db.get_decision(decision_id)

    def llm_response_at(self, strategy, ts):
        return self.db.llm_response_at(strategy, ts)

    def per_trade_reflection_exists(self, strategy, position_id):
        return self.db.per_trade_reflection_exists(strategy, position_id)

    def publish_playbook(self, *args, **kwargs):
        return self.db.publish_playbook(*args, **kwargs)

    def latest_playbook(self, strategy):
        return self.db.latest_playbook(strategy)

    def get_playbook(self, version):
        return self.db.get_playbook(version)

    def retire_playbook(self, version, retired_ts):
        return self.db.retire_playbook(version, retired_ts)

    # --- Phase-4 proposals (the approval gate) ---
    def create_proposal(self, *args, **kwargs):
        return self.db.create_proposal(*args, **kwargs)

    def pending_proposals(self, strategy=None):
        return self.db.pending_proposals(strategy)

    def recent_proposals(self, strategy=None, limit=100):
        return self.db.recent_proposals(strategy, limit)

    def get_proposal(self, proposal_id):
        return self.db.get_proposal(proposal_id)

    def decide_proposal(self, proposal_id, status, decided_by, decided_ts):
        return self.db.decide_proposal(proposal_id, status, decided_by, decided_ts)

    def apply_playbook_proposal(self, proposal_id, version, strategy, rules, applied_by, now_ms):
        return self.db.apply_playbook_proposal(proposal_id, version, strategy, rules,
                                               applied_by, now_ms)

    def close(self):
        return self.db.close()
