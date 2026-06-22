import json
import os
import sqlite3
from homing_trade.models import Candle, Position

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategies (
    name TEXT PRIMARY KEY,
    starting_balance REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS wallets (
    name TEXT PRIMARY KEY,
    balance REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    size REAL NOT NULL,
    leverage REAL NOT NULL,
    margin REAL NOT NULL,
    stop_price REAL NOT NULL,
    opened_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    position_id INTEGER,
    side TEXT NOT NULL,
    action TEXT NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    fee REAL NOT NULL,
    pnl REAL NOT NULL,
    ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS equity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    equity REAL NOT NULL,
    ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS decision_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    ts INTEGER NOT NULL,
    candle_time INTEGER NOT NULL,
    action TEXT NOT NULL,
    confidence REAL NOT NULL,
    reason TEXT NOT NULL,
    indicators_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy    TEXT    NOT NULL,
    ts          INTEGER NOT NULL,
    backend     TEXT,
    model       TEXT,
    action      TEXT,
    confidence  REAL,
    observation TEXT,    -- what the model saw on the 1m + 15m charts
    prediction  TEXT,    -- what it expects price to do next
    rationale   TEXT,    -- why that leads to the decision
    raw         TEXT,    -- full raw model response (envelope/text)
    error       TEXT     -- error message if the model call failed
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candles (
    pair     TEXT    NOT NULL,
    interval TEXT    NOT NULL,
    time     INTEGER NOT NULL,
    open     REAL    NOT NULL,
    high     REAL    NOT NULL,
    low      REAL    NOT NULL,
    close    REAL    NOT NULL,
    volume   REAL    NOT NULL,
    source   TEXT    NOT NULL,
    PRIMARY KEY (pair, interval, time)
);
"""

# Forward-only schema migrations. The base SCHEMA above (CREATE ... IF NOT EXISTS)
# bootstraps a fresh DB; MIGRATIONS carry every change made after that initial schema
# so existing databases evolve in place. Each migration is idempotent DDL keyed by an
# integer version; state['schema_version'] records how far a given DB has been migrated.
# To change the schema: add the next integer key here and bump SCHEMA_VERSION — never
# edit a released migration or renumber the existing ones.
SCHEMA_VERSION = 13

# Each migration is a LIST of single SQL statements (no trailing ';'). _migrate() applies all
# statements of a version PLUS its schema_version bump inside one transaction, so a failure
# rolls the whole version back rather than leaving the DB half-migrated. The `ver > current`
# guard guarantees each version runs exactly once per DB; `CREATE ... IF NOT EXISTS` is used
# where possible, while `ALTER TABLE ADD COLUMN` (not idempotent in SQLite) is safe because of
# that once-only guard + the atomic rollback above.
MIGRATIONS = {
    # v1: indexes that make the reflection / leaderboard joins cheap (Phase 2 reads these hot).
    1: [
        "CREATE INDEX IF NOT EXISTS idx_decision_log_strategy_ts ON decision_log(strategy, ts)",
        "CREATE INDEX IF NOT EXISTS idx_llm_responses_strategy_ts ON llm_responses(strategy, ts)",
        "CREATE INDEX IF NOT EXISTS idx_trades_strategy_ts ON trades(strategy, ts)",
        "CREATE INDEX IF NOT EXISTS idx_trades_position_id ON trades(position_id)",
    ],
    # v2: Phase-2 observability ledger — row-level provenance (replayable decisions),
    # execution fidelity (slippage), and risk visibility. Columns are nullable; population
    # is wired in follow-up PRs.
    2: [
        # decision_log: the full story of each decision — what was intended, what was taken,
        # why it was blocked, and the regime/vol context at decision time.
        "ALTER TABLE decision_log ADD COLUMN decision_id TEXT",
        "ALTER TABLE decision_log ADD COLUMN intended_action TEXT",
        "ALTER TABLE decision_log ADD COLUMN taken_action TEXT",
        "ALTER TABLE decision_log ADD COLUMN rejection_rationale TEXT",
        "ALTER TABLE decision_log ADD COLUMN regime TEXT",
        "ALTER TABLE decision_log ADD COLUMN realized_vol REAL",
        "ALTER TABLE decision_log ADD COLUMN prompt_version TEXT",
        "ALTER TABLE decision_log ADD COLUMN playbook_version TEXT",
        # llm_responses: make a model consult fully replayable.
        "ALTER TABLE llm_responses ADD COLUMN prompt_version TEXT",
        "ALTER TABLE llm_responses ADD COLUMN prompt_hash TEXT",
        "ALTER TABLE llm_responses ADD COLUMN next_check_in_sec INTEGER",
        "ALTER TABLE llm_responses ADD COLUMN requested_charts TEXT",
        # trades: capture the decision price and realized slippage of each fill.
        "ALTER TABLE trades ADD COLUMN decision_price REAL",
        "ALTER TABLE trades ADD COLUMN slippage REAL",
        # risk_events: DailyRiskGuard vetoes / kill-switch trips, so they are observable not silent.
        "CREATE TABLE IF NOT EXISTS risk_events ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, strategy TEXT,"
        " kind TEXT NOT NULL, reason TEXT, notional REAL)",
        "CREATE INDEX IF NOT EXISTS idx_risk_events_ts ON risk_events(ts)",
    ],
    # v3: forward-only market-regime time series (one row per pair/interval/candle time),
    # computed at decision time from indicators.classify_regime.
    3: [
        "CREATE TABLE IF NOT EXISTS regimes ("
        " pair TEXT NOT NULL, interval TEXT NOT NULL, time INTEGER NOT NULL,"
        " regime TEXT, adx REAL, ema_slope REAL, realized_vol REAL,"
        " PRIMARY KEY (pair, interval, time))",
    ],
    # v4: denormalized one-row-per-completed-trade table — the single row the reflection
    # loop reads. Built from trades (open→close join). The enrichment columns
    # (decision_id, regime_at_entry, mae, mfe, prediction_correct, exit_reason) are populated
    # in follow-up slices; realized_at_ts carries the outcome embargo (no look-ahead).
    4: [
        "CREATE TABLE IF NOT EXISTS trade_outcomes ("
        " position_id INTEGER PRIMARY KEY, strategy TEXT, side TEXT,"
        " entry_price REAL, exit_price REAL, entry_ts INTEGER, exit_ts INTEGER, size REAL,"
        " fees REAL, slippage REAL, realized_pnl REAL, pnl_pct REAL, holding_period_ms INTEGER,"
        " exit_reason TEXT, regime_at_entry TEXT, decision_id TEXT,"
        " mae REAL, mfe REAL, prediction_correct INTEGER, realized_at_ts INTEGER)",
        "CREATE INDEX IF NOT EXISTS idx_trade_outcomes_strategy ON trade_outcomes(strategy)",
    ],
    # v5: record why each position closed (signal / stop / liquidation / manual) on the CLOSE trade.
    5: [
        "ALTER TABLE trades ADD COLUMN exit_reason TEXT",
    ],
    # v6: link each OPEN trade (and its outcome) to the decision that triggered it + the regime
    # at entry — for outcome->decision->rationale traceability and per-regime attribution.
    6: [
        "ALTER TABLE trades ADD COLUMN decision_id TEXT",
        "ALTER TABLE trades ADD COLUMN regime_at_entry TEXT",
    ],
    # v7: Phase-4 learn->correct foundation. `reflections` = the AI's batched/per-trade
    # retrospection over trade_outcomes (lesson + a proposed playbook version). `playbooks` =
    # append-only, versioned rule sets (a published version's rules_json is never mutated;
    # only retired_ts is ever set). Both are model-authored (Hierarchy of Truth).
    7: [
        """CREATE TABLE IF NOT EXISTS reflections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL,
            kind TEXT NOT NULL,                 -- 'per_trade' | 'periodic'
            ts INTEGER NOT NULL,
            batch_from_ts INTEGER,
            batch_to_ts INTEGER,
            trade_ids_json TEXT,
            metrics_json TEXT,
            lesson TEXT,
            new_playbook_version TEXT,
            model TEXT,
            raw TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS playbooks (
            version TEXT PRIMARY KEY,           -- globally-unique version id
            strategy TEXT NOT NULL,
            created_ts INTEGER NOT NULL,
            rules_json TEXT NOT NULL,
            parent_version TEXT,
            retired_ts INTEGER                  -- set when superseded; rules_json is never UPDATEd
        )""",
        "CREATE INDEX IF NOT EXISTS idx_reflections_strategy_ts ON reflections(strategy, ts)",
        "CREATE INDEX IF NOT EXISTS idx_playbooks_strategy ON playbooks(strategy, created_ts)",
    ],
    # v8: the proposals approval-gate — the single chokepoint between an AI suggestion and an
    # applied change. Every row starts 'pending'; a human (or web UI / #comms) flips it to
    # 'approved'/'rejected'. NOTHING is applied here; this table only records the request and
    # the decision. Protected fields (risk limits / kill-switch / secrets / live-arming) can
    # never even be proposed — enforced in create_proposal, not just at apply time.
    8: [
        """CREATE TABLE IF NOT EXISTS proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT,
            kind TEXT NOT NULL,                 -- 'param'|'prompt'|'playbook'|'strategy_toggle'
            payload_json TEXT NOT NULL,
            rationale TEXT,
            status TEXT NOT NULL DEFAULT 'pending',  -- 'pending'|'approved'|'rejected'
            created_ts INTEGER NOT NULL,
            decided_ts INTEGER,
            decided_by TEXT,
            source_reflection_id INTEGER
        )""",
        "CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status, created_ts)",
    ],
    # v9: the APPLY step's provenance — when an approved proposal's change was actually applied,
    # by whom, and the result (e.g. the published playbook version). `status` stays 'approved'
    # (the human's decision); `applied_ts` is the distinct fact that the change took effect, so
    # an approved-but-not-yet-applied proposal is observable and apply stays idempotent.
    9: [
        "ALTER TABLE proposals ADD COLUMN applied_ts INTEGER",
        "ALTER TABLE proposals ADD COLUMN applied_by TEXT",
        "ALTER TABLE proposals ADD COLUMN applied_result TEXT",
    ],
    # v10: per-provider cost accounting (Phase 5 #4). One row per AI consult: the token usage and
    # (best-effort) USD cost, attributed to the strategy/model/backend. Machine-written from the
    # provider response — a mechanical fact, never model-authored, so it is audit-truth. usd/tokens
    # are nullable (a provider may not report them; local llama has no price).
    10: [
        """CREATE TABLE IF NOT EXISTS cost_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL,
            ts INTEGER NOT NULL,
            model TEXT,
            backend TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            usd REAL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_cost_ledger_strategy ON cost_ledger(strategy, ts)",
    ],
    # v11: a generic external-signal cache (Phase 6). One latest row per (source, key) — e.g.
    # source='fng', key='latest' — carrying the parsed value (value_json) + fetched_at, so every
    # external pull (Fear&Greed, funding, on-chain, ...) is cached, rate-limit-friendly, and
    # replayable. Machine-written from the fetch (a mechanical fact), never model-authored ->
    # audit-truth. `ts` is the upstream observation time; `fetched_at` is when we pulled it.
    11: [
        """CREATE TABLE IF NOT EXISTS signal_cache (
            source TEXT NOT NULL,
            key TEXT NOT NULL,
            ts INTEGER,
            value_json TEXT NOT NULL,
            fetched_at INTEGER NOT NULL,
            PRIMARY KEY (source, key)
        )""",
    ],
    # v12: A/B variant-experiment ledger (Phase 7 #4). One row per honest two-variant test —
    # a definitional `hypothesis` LABEL (e.g. "supertrend>ma_trend on pnl_pct"; NOT free model prose),
    # the two variants, the metric, the pre-registered min-detectable-effect, the window, the realized
    # sample sizes (n_a/n_b), and the mechanically-computed two-sided p_value + result + the multiple-
    # comparison correction applied. All fields are mechanical bookkeeping (no model-authored text) ->
    # audit-truth. A running experiment has end_ts/result/p_value NULL; conclude_experiment fills them.
    12: [
        """CREATE TABLE IF NOT EXISTS experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hypothesis TEXT NOT NULL,
            variant_a TEXT NOT NULL,
            variant_b TEXT NOT NULL,
            metric TEXT NOT NULL,
            mde REAL,
            start_ts INTEGER NOT NULL,
            end_ts INTEGER,
            n_a INTEGER,
            n_b INTEGER,
            result TEXT,                        -- NULL=running | 'a_wins'|'b_wins'|'inconclusive'
            p_value REAL,
            correction_method TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_experiments_start ON experiments(start_ts)",
    ],
    # v13: continuous walk-forward backtest results (Phase 7 #7). One row per strategy per run of the
    # continuous backtest job, carrying the mechanically-computed OOS aggregate AND the trusted
    # (post-cutoff, profit-mirage-guarded) subset. All metrics are machine-computed -> audit-truth.
    13: [
        """CREATE TABLE IF NOT EXISTS backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            strategy TEXT NOT NULL,
            pair TEXT, interval TEXT,
            train INTEGER, test INTEGER, window INTEGER, cutoff_ms INTEGER,
            folds INTEGER,
            oos_return_pct REAL, oos_sharpe REAL, oos_hit_rate REAL, oos_max_dd REAL, oos_trades INTEGER,
            trusted_folds INTEGER, trusted_return_pct REAL, trusted_sharpe REAL, trusted_hit_rate REAL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_backtest_results_strategy_ts ON backtest_results(strategy, ts)",
    ],
}


# ── Hierarchy of Truth ────────────────────────────────────────────────────────────────
# Every table is in exactly one of two classes. The split is a hard governance invariant
# for the autonomous loop — see docs/hierarchy-of-truth.md for the full rationale.
#
#   AUDIT_TRUTH_TABLES   — machine-written ground truth. Prices, fills, balances, equity,
#                          mechanically-derived classifications/metrics, and guard events.
#                          No model output may ever author or edit a row here; this is the
#                          record a human (or the bot's own self-query) can trust absolutely.
#   MODEL_AUTHORED_TABLES — the ONLY tables allowed to carry free model-authored text
#                          (observation / prediction / rationale / lesson / playbook rule).
#                          Quarantining model prose here keeps it from contaminating the
#                          ground truth and is what makes mechanical scoring (and the
#                          reward-hacking / Oracle-Fallacy guards) meaningful.
#
# Enforcement: SelfQuery touches only read methods (asserted by test_selfquery), and
# test_hierarchy_of_truth asserts this classification stays COMPLETE (every live table is
# classified) and DISJOINT as the schema grows.
AUDIT_TRUTH_TABLES = frozenset({
    "strategies", "wallets", "positions", "trades", "equity",
    "candles", "state", "risk_events", "regimes", "trade_outcomes", "cost_ledger", "signal_cache",
    "experiments", "backtest_results",
})
MODEL_AUTHORED_TABLES = frozenset({
    "decision_log", "llm_responses", "reflections", "playbooks", "proposals",
})

# The proposal approval-gate. `proposals` rows are model-authored suggestions that a human
# must approve before anything applies — and these zones can never even be PROPOSED, let alone
# applied: hard risk limits, the kill-switch, leverage caps, per-trade risk sizing, execution
# fidelity, the live-arming flags, alert routing, and anything secret.
#
# The exact set below is enumerated from the REAL config.Config fields (not from memory); the
# family/secret SUBSTRINGS then provide fail-closed coverage so a NEW risk-/leverage-/live-/
# secret-named field added to config later is protected by default. None of the legitimate
# tunables (ema/rsi/macd/bollinger/donchian/rl_*/lookback/threshold/period…) contain these
# substrings, so there is no over-block. test_proposals seeds a coverage check from config.
PROPOSAL_KINDS = frozenset({"param", "prompt", "playbook", "strategy_toggle"})
PROTECTED_PROPOSAL_FIELDS = frozenset({
    # leverage caps + per-trade risk sizing (leverage_min defeats leverage_max via effective_leverage)
    "leverage", "leverage_min", "leverage_max", "risk_pct", "stop_pct",
    # daily caps / kill-switch / master switch
    "max_trade_amount_per_day", "max_daily_loss", "trading_enabled",
    # execution fidelity + volatility guard + committee gate (tampering distorts risk/PnL)
    "fee", "slippage", "risk_vol_window", "risk_vol_threshold", "committee_threshold",
    # regime-aware portfolio gate (sizing weight + committee entry bar — human-gated, not auto-tuned)
    "regime_filter_enabled", "regime_unfavored_weight", "regime_committee_threshold_scale",
    # profit-mirage trust cutoff — loosening it would let pre-cutoff mirages promote; human-gated only
    "trust_cutoff_iso",
    # live-arming
    "live_enabled", "live_dry_run", "dry_run", "live",
    # alert routing + secret/env names
    "webhook_url", "discord_webhook_env", "telegram_token_env", "telegram_chat_id_env",
    "coindcx_key_env", "coindcx_secret_env",
})
_PROTECTED_SUBSTRINGS = (
    "secret", "token", "password", "webhook", "api_key", "apikey",     # secrets / keys
    "leverage", "risk", "dry_run", "daily_loss", "margin",             # risk / leverage families
    "live_", "trading_enabled", "_key_env", "chat_id_env",             # arming + secret-env names
)  # NOTE: no bare "kill" — no real field uses it (kill-switch == max_daily_loss, in the exact
   # set) and it would false-match "enabled_skills" (s-kill-s).


def _is_protected(token: str) -> bool:
    t = token.lower()
    return t in PROTECTED_PROPOSAL_FIELDS or any(s in t for s in _PROTECTED_SUBSTRINGS)


def _assert_no_protected_fields(payload, *, scan_values=False):
    """Raise ValueError if a proposal payload references any protected field (at any depth).

    Always scans dict KEYS. For `param` proposals (`scan_values=True`) it ALSO scans string
    VALUES, so a field-as-value payload like {"field": "leverage_min", "value": 99} can't slip
    a protected name past a key-only check. Value-scanning is NOT applied to prompt/playbook
    kinds, whose values are free model prose that may legitimately mention 'risk'/'leverage'."""
    def tokens(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield str(k)
                yield from tokens(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                yield from tokens(v)
        elif scan_values and isinstance(obj, str):
            yield obj
    for tok in tokens(payload):
        if _is_protected(tok):
            raise ValueError(f"proposal may not touch protected field: {tok}")


class Database:
    def __init__(self, path: str):
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        # WAL lets the web UI read concurrently while the engine thread writes.
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
        except sqlite3.OperationalError:
            pass
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Apply forward-only migrations this DB hasn't seen yet.

        Each version's statements and its schema_version bump commit atomically
        (explicit BEGIN/COMMIT, ROLLBACK on error), so a failed migration leaves the
        DB at its previous version with no partial DDL applied. SQLite DDL is
        transactional, which makes the rollback real.
        """
        row = self.conn.execute("SELECT value FROM state WHERE key='schema_version'").fetchone()
        current = int(row["value"]) if row else 0
        pending = [v for v in sorted(MIGRATIONS) if v > current]
        if not pending:
            return
        prev_isolation = self.conn.isolation_level
        self.conn.isolation_level = None  # honor our explicit BEGIN/COMMIT (no implicit txn)
        try:
            for ver in pending:
                try:
                    self.conn.execute("BEGIN")
                    for stmt in MIGRATIONS[ver]:
                        self.conn.execute(stmt)
                    self.conn.execute(
                        "INSERT INTO state(key, value) VALUES('schema_version', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (str(ver),),
                    )
                    self.conn.execute("COMMIT")
                except Exception:
                    self.conn.execute("ROLLBACK")
                    raise
        finally:
            self.conn.isolation_level = prev_isolation

    def schema_version(self) -> int:
        row = self.conn.execute("SELECT value FROM state WHERE key='schema_version'").fetchone()
        return int(row["value"]) if row else 0

    def ensure_strategy(self, name: str, starting_balance: float) -> None:
        cur = self.conn.cursor()
        cur.execute("INSERT OR IGNORE INTO strategies(name, starting_balance) VALUES(?,?)",
                    (name, starting_balance))
        cur.execute("INSERT OR IGNORE INTO wallets(name, balance) VALUES(?,?)",
                    (name, starting_balance))
        self.conn.commit()

    def get_balance(self, name: str) -> float:
        row = self.conn.execute("SELECT balance FROM wallets WHERE name=?", (name,)).fetchone()
        return float(row["balance"]) if row else 0.0

    def set_balance(self, name: str, balance: float) -> None:
        self.conn.execute("UPDATE wallets SET balance=? WHERE name=?", (balance, name))
        self.conn.commit()

    def open_position(self, pos: Position) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """INSERT INTO positions(strategy, side, entry_price, size, leverage,
               margin, stop_price, opened_at, status)
               VALUES(?,?,?,?,?,?,?,?,'open')""",
            (pos.strategy, pos.side, pos.entry_price, pos.size, pos.leverage,
             pos.margin, pos.stop_price, pos.opened_at),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def close_position(self, position_id: int) -> None:
        self.conn.execute("UPDATE positions SET status='closed' WHERE id=?", (position_id,))
        self.conn.commit()

    def get_open_position(self, name: str) -> Position | None:
        row = self.conn.execute(
            "SELECT * FROM positions WHERE strategy=? AND status='open' ORDER BY id DESC LIMIT 1",
            (name,),
        ).fetchone()
        if not row:
            return None
        return Position(
            id=row["id"], strategy=row["strategy"], side=row["side"],
            entry_price=row["entry_price"], size=row["size"], leverage=row["leverage"],
            margin=row["margin"], stop_price=row["stop_price"], opened_at=row["opened_at"],
            status=row["status"],
        )

    def record_trade(self, strategy, position_id, side, action, price, size, fee, pnl, ts,
                     *, decision_price=None, slippage=None, exit_reason=None,
                     decision_id=None, regime_at_entry=None) -> None:
        self.conn.execute(
            """INSERT INTO trades(strategy, position_id, side, action, price, size, fee, pnl, ts,
                                  decision_price, slippage, exit_reason, decision_id, regime_at_entry)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (strategy, position_id, side, action, price, size, fee, pnl, ts,
             decision_price, slippage, exit_reason, decision_id, regime_at_entry),
        )
        self.conn.commit()

    def record_equity(self, strategy, equity, ts) -> None:
        self.conn.execute("INSERT INTO equity(strategy, equity, ts) VALUES(?,?,?)",
                          (strategy, equity, ts))
        self.conn.commit()

    def log_decision(self, strategy, ts, candle_time, action, confidence, reason, indicators,
                     *, decision_id=None, intended_action=None, taken_action=None,
                     rejection_rationale=None, regime=None, realized_vol=None,
                     prompt_version=None, playbook_version=None) -> None:
        self.conn.execute(
            """INSERT INTO decision_log(
                   strategy, ts, candle_time, action, confidence, reason, indicators_json,
                   decision_id, intended_action, taken_action, rejection_rationale,
                   regime, realized_vol, prompt_version, playbook_version)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (strategy, ts, candle_time, action, confidence, reason, json.dumps(indicators),
             decision_id, intended_action, taken_action, rejection_rationale,
             regime, realized_vol, prompt_version, playbook_version),
        )
        self.conn.commit()

    def record_llm_response(self, strategy, ts, backend, model, action, confidence,
                            observation, prediction, rationale, raw, error,
                            *, next_check_in_sec=None, requested_charts=None,
                            prompt_version=None, prompt_hash=None) -> None:
        self.conn.execute(
            """INSERT INTO llm_responses(strategy, ts, backend, model, action, confidence,
                                         observation, prediction, rationale, raw, error,
                                         next_check_in_sec, requested_charts,
                                         prompt_version, prompt_hash)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (strategy, ts, backend, model, action, confidence,
             observation, prediction, rationale, raw, error, next_check_in_sec,
             json.dumps(requested_charts) if requested_charts is not None else None,
             prompt_version, prompt_hash),
        )
        self.conn.commit()

    def recent_llm_responses(self, strategy=None, limit=20):
        if strategy is not None:
            return self.conn.execute(
                "SELECT * FROM llm_responses WHERE strategy=? ORDER BY id DESC LIMIT ?",
                (strategy, limit)).fetchall()
        return self.conn.execute(
            "SELECT * FROM llm_responses ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def latest_llm_rationale(self, strategy):
        """Most recent rationale for a strategy (for enriching trade alerts); '' if none."""
        row = self.conn.execute(
            "SELECT rationale FROM llm_responses WHERE strategy=? AND error IS NULL "
            "ORDER BY id DESC LIMIT 1", (strategy,)).fetchone()
        return (row["rationale"] or "") if row else ""

    def record_cost(self, strategy, ts, model, backend, prompt_tokens, completion_tokens, usd) -> None:
        """Append one AI-consult cost row (Phase 5 #4). Machine-written audit-truth."""
        self.conn.execute(
            "INSERT INTO cost_ledger(strategy, ts, model, backend, prompt_tokens,"
            " completion_tokens, usd) VALUES(?,?,?,?,?,?,?)",
            (strategy, ts, model, backend, prompt_tokens, completion_tokens, usd))
        self.conn.commit()

    def cost_summary(self, strategy=None) -> dict:
        """Per-strategy cost rollup: {strategy: {calls, prompt_tokens, completion_tokens,
        total_tokens, usd}} over all cost_ledger rows (optionally one strategy). NULL tokens/usd
        sum as 0; `calls` counts consults."""
        q = ("SELECT strategy, COUNT(*) AS calls,"
             " COALESCE(SUM(prompt_tokens),0) AS pt, COALESCE(SUM(completion_tokens),0) AS ct,"
             " COALESCE(SUM(usd),0.0) AS usd FROM cost_ledger ")
        if strategy is not None:
            rows = self.conn.execute(q + "WHERE strategy=? GROUP BY strategy", (strategy,)).fetchall()
        else:
            rows = self.conn.execute(q + "GROUP BY strategy").fetchall()
        return {r["strategy"]: {"calls": r["calls"], "prompt_tokens": r["pt"],
                                "completion_tokens": r["ct"], "total_tokens": r["pt"] + r["ct"],
                                "usd": r["usd"]} for r in rows}

    def upsert_signal(self, source, key, ts, value, fetched_at) -> None:
        """Cache the latest external-signal reading for (source, key) (Phase 6). `value` is JSON-
        serializable; `ts` is the upstream observation time, `fetched_at` when we pulled it."""
        self.conn.execute(
            "INSERT INTO signal_cache(source, key, ts, value_json, fetched_at) VALUES(?,?,?,?,?)"
            " ON CONFLICT(source, key) DO UPDATE SET ts=excluded.ts,"
            " value_json=excluded.value_json, fetched_at=excluded.fetched_at",
            (source, key, ts, json.dumps(value), fetched_at))
        self.conn.commit()

    def get_signal(self, source, key) -> dict | None:
        """The cached reading for (source, key) -> {source, key, ts, value, fetched_at} or None."""
        row = self.conn.execute(
            "SELECT * FROM signal_cache WHERE source=? AND key=?", (source, key)).fetchone()
        if not row:
            return None
        return {"source": row["source"], "key": row["key"], "ts": row["ts"],
                "value": json.loads(row["value_json"]), "fetched_at": row["fetched_at"]}

    def all_signals(self) -> list:
        """Every cached signal row -> [{source, key, ts, value, fetched_at}], newest fetch first
        (for cache observability / the signal-status panel)."""
        rows = self.conn.execute("SELECT * FROM signal_cache ORDER BY fetched_at DESC").fetchall()
        return [{"source": r["source"], "key": r["key"], "ts": r["ts"],
                 "value": json.loads(r["value_json"]), "fetched_at": r["fetched_at"]} for r in rows]

    # --- A/B variant experiments (Phase 7 #4): mechanical bookkeeping, audit-truth ---
    @staticmethod
    def _experiment_row(r):
        return {"id": r["id"], "hypothesis": r["hypothesis"], "variant_a": r["variant_a"],
                "variant_b": r["variant_b"], "metric": r["metric"], "mde": r["mde"],
                "start_ts": r["start_ts"], "end_ts": r["end_ts"], "n_a": r["n_a"], "n_b": r["n_b"],
                "result": r["result"], "p_value": r["p_value"],
                "correction_method": r["correction_method"],
                # end_ts is THE conclusion marker (always set by conclude, always NULL at create),
                # so status is correct even if a conclusion records a None/'inconclusive' result.
                "status": "running" if r["end_ts"] is None else "concluded"}

    def create_experiment(self, hypothesis, variant_a, variant_b, metric, start_ts,
                          *, mde=None, correction_method=None) -> int:
        """Register a running A/B experiment (end_ts/n/result/p_value left NULL). Returns its id."""
        cur = self.conn.execute(
            "INSERT INTO experiments(hypothesis, variant_a, variant_b, metric, mde, start_ts,"
            " correction_method) VALUES(?,?,?,?,?,?,?)",
            (hypothesis, variant_a, variant_b, metric, mde, start_ts, correction_method))
        self.conn.commit()
        return cur.lastrowid

    def conclude_experiment(self, experiment_id, end_ts, n_a, n_b, result, p_value,
                            *, correction_method=None) -> None:
        """Fill in an experiment's realized window/sample/result. correction_method overrides the
        registered one only when given (Phase 7 #5 sets it when a correction is applied)."""
        if correction_method is None:
            self.conn.execute(
                "UPDATE experiments SET end_ts=?, n_a=?, n_b=?, result=?, p_value=? WHERE id=?",
                (end_ts, n_a, n_b, result, p_value, experiment_id))
        else:
            self.conn.execute(
                "UPDATE experiments SET end_ts=?, n_a=?, n_b=?, result=?, p_value=?,"
                " correction_method=? WHERE id=?",
                (end_ts, n_a, n_b, result, p_value, correction_method, experiment_id))
        self.conn.commit()

    def get_experiment(self, experiment_id) -> dict | None:
        row = self.conn.execute("SELECT * FROM experiments WHERE id=?", (experiment_id,)).fetchone()
        return self._experiment_row(row) if row else None

    def list_experiments(self, *, status=None) -> list:
        """All experiments newest-start first; status ∈ {None, 'running', 'concluded'}."""
        rows = self.conn.execute("SELECT * FROM experiments ORDER BY start_ts DESC, id DESC").fetchall()
        out = [self._experiment_row(r) for r in rows]
        return [e for e in out if status is None or e["status"] == status]

    def experiment_search_budget(self, start_ts, end_ts) -> int:
        """How many experiments were STARTED in [start_ts, end_ts] — the search-budget count that
        Phase 7 #5's multiple-comparison correction (Bonferroni/BH) divides significance over."""
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM experiments WHERE start_ts BETWEEN ? AND ?",
            (start_ts, end_ts)).fetchone()
        return row["n"]

    # --- continuous backtest results (Phase 7 #7): mechanical metrics, audit-truth ---
    def record_backtest_result(self, ts, strategy, *, pair, interval, train, test, window,
                               cutoff_ms, oos, trusted) -> int:
        """Append one backtest run for `strategy`. `oos` and `trusted` are walk_forward aggregate
        dicts (the trusted one over post-cutoff folds). Returns the row id."""
        cur = self.conn.execute(
            "INSERT INTO backtest_results(ts, strategy, pair, interval, train, test, window,"
            " cutoff_ms, folds, oos_return_pct, oos_sharpe, oos_hit_rate, oos_max_dd, oos_trades,"
            " trusted_folds, trusted_return_pct, trusted_sharpe, trusted_hit_rate)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, strategy, pair, interval, train, test, window, cutoff_ms,
             oos.get("folds"), oos.get("compounded_return_pct"), oos.get("mean_sharpe"),
             oos.get("hit_rate"), oos.get("worst_drawdown"), oos.get("total_trades"),
             trusted.get("folds"), trusted.get("compounded_return_pct"), trusted.get("mean_sharpe"),
             trusted.get("hit_rate")))
        self.conn.commit()
        return cur.lastrowid

    @staticmethod
    def _backtest_row(r):
        return {k: r[k] for k in r.keys()}

    def recent_backtest_results(self, limit=50, strategy=None) -> list:
        if strategy is not None:
            rows = self.conn.execute(
                "SELECT * FROM backtest_results WHERE strategy=? ORDER BY ts DESC, id DESC LIMIT ?",
                (strategy, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM backtest_results ORDER BY ts DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [self._backtest_row(r) for r in rows]

    def latest_backtest_per_strategy(self) -> list:
        """The most recent backtest row for each strategy (for the UI), newest first."""
        rows = self.conn.execute(
            "SELECT b.* FROM backtest_results b JOIN ("
            "  SELECT strategy, MAX(ts) AS mts FROM backtest_results GROUP BY strategy) m"
            " ON b.strategy=m.strategy AND b.ts=m.mts ORDER BY b.ts DESC, b.id DESC").fetchall()
        # collapse any same-ts ties to one row per strategy
        seen, out = set(), []
        for r in rows:
            if r["strategy"] in seen:
                continue
            seen.add(r["strategy"])
            out.append(self._backtest_row(r))
        return out

    def get_state(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO state(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def save_candles(self, pair, interval, candles, source) -> int:
        rows = [(pair, interval, c.time, c.open, c.high, c.low, c.close, c.volume, source)
                for c in candles]
        self.conn.executemany(
            """INSERT INTO candles(pair, interval, time, open, high, low, close, volume, source)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(pair, interval, time) DO UPDATE SET
                 open=excluded.open, high=excluded.high, low=excluded.low,
                 close=excluded.close, volume=excluded.volume, source=excluded.source""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_candles_range(self, pair, interval, start_ms, end_ms, source="all"):
        q = ("SELECT time, open, high, low, close, volume FROM candles "
             "WHERE pair=? AND interval=? AND time>=? AND time<=?")
        params = [pair, interval, start_ms, end_ms]
        if source != "all":
            q += " AND source=?"
            params.append(source)
        q += " ORDER BY time ASC"
        rows = self.conn.execute(q, params).fetchall()
        return [Candle(open=r["open"], high=r["high"], low=r["low"], close=r["close"],
                       volume=r["volume"], time=r["time"]) for r in rows]

    def get_candle_bounds(self, pair, interval):
        row = self.conn.execute(
            "SELECT MIN(time) AS mn, MAX(time) AS mx FROM candles WHERE pair=? AND interval=?",
            (pair, interval)).fetchone()
        if row is None or row["mn"] is None:
            return None
        return (int(row["mn"]), int(row["mx"]))

    def count_candles(self, pair, interval, source="all") -> int:
        q = "SELECT COUNT(*) AS c FROM candles WHERE pair=? AND interval=?"
        params = [pair, interval]
        if source != "all":
            q += " AND source=?"
            params.append(source)
        return int(self.conn.execute(q, params).fetchone()["c"])

    def recent_close_pnls(self, strategy, limit):
        rows = self.conn.execute(
            "SELECT pnl FROM trades WHERE strategy=? AND action='CLOSE' ORDER BY id DESC LIMIT ?",
            (strategy, limit)).fetchall()
        return [float(r["pnl"]) for r in rows]

    def closed_pnls(self, strategy):
        """All realized PnLs for CLOSE trades, oldest-first (win/loss + drawdown stats)."""
        rows = self.conn.execute(
            "SELECT pnl FROM trades WHERE strategy=? AND action='CLOSE' ORDER BY id ASC",
            (strategy,)).fetchall()
        return [float(r["pnl"]) for r in rows]

    def equity_series(self, strategy):
        """Equity snapshots for a strategy, oldest-first."""
        rows = self.conn.execute(
            "SELECT equity FROM equity WHERE strategy=? ORDER BY ts ASC",
            (strategy,)).fetchall()
        return [float(r["equity"]) for r in rows]

    def trades_after(self, last_id):
        rows = self.conn.execute(
            "SELECT id, strategy, side, action, price, size, pnl FROM trades WHERE id>? ORDER BY id ASC",
            (last_id,)).fetchall()
        return [dict(r) for r in rows]

    def max_trade_id(self) -> int:
        row = self.conn.execute("SELECT MAX(id) AS m FROM trades").fetchone()
        return int(row["m"]) if row and row["m"] is not None else 0

    def strategy_names(self):
        return [r["name"] for r in self.conn.execute(
            "SELECT name FROM strategies ORDER BY name")]

    def latest_equity(self, strategy):
        row = self.conn.execute(
            "SELECT equity FROM equity WHERE strategy=? ORDER BY ts DESC LIMIT 1",
            (strategy,)).fetchone()
        return float(row["equity"]) if row else None

    def recent_trades(self, limit):
        return [dict(r) for r in self.conn.execute(
            "SELECT strategy, side, action, price, size, pnl, ts FROM trades ORDER BY id DESC LIMIT ?",
            (limit,))]

    def recent_decisions(self, limit):
        return [dict(r) for r in self.conn.execute(
            "SELECT strategy, action, confidence, reason, ts FROM decision_log ORDER BY id DESC LIMIT ?",
            (limit,))]

    def taken_action_counts(self, strategy):
        """How many decisions resolved to each taken_action (LONG/HOLD/BLOCKED/PAUSED/...)."""
        rows = self.conn.execute(
            "SELECT taken_action, COUNT(*) AS n FROM decision_log "
            "WHERE strategy=? AND taken_action IS NOT NULL GROUP BY taken_action",
            (strategy,)).fetchall()
        return {r["taken_action"]: int(r["n"]) for r in rows}

    def reset_paper_ledger(self):
        """Wipe the paper ledger (wallets/positions/trades/equity/decisions/llm + strategies)
        and the last-candle cursor. Cached candles are kept."""
        for t in ("trades", "positions", "equity", "decision_log",
                  "llm_responses", "wallets", "strategies"):
            self.conn.execute(f"DELETE FROM {t}")
        self.conn.execute("DELETE FROM state WHERE key='last_candle_time'")
        self.conn.commit()

    def record_risk_event(self, ts, strategy, kind, reason, notional=None):
        """Record a risk-guard event (kind e.g. 'veto' | 'halt') so it is observable."""
        self.conn.execute(
            "INSERT INTO risk_events(ts, strategy, kind, reason, notional) VALUES(?,?,?,?,?)",
            (ts, strategy, kind, reason, notional))
        self.conn.commit()

    def recent_risk_events(self, limit=50):
        return [dict(r) for r in self.conn.execute(
            "SELECT ts, strategy, kind, reason, notional FROM risk_events ORDER BY id DESC LIMIT ?",
            (limit,))]

    def record_regime(self, pair, interval, time, regime, adx=None, ema_slope=None, realized_vol=None):
        """Upsert the market regime for a (pair, interval, candle time)."""
        self.conn.execute(
            """INSERT INTO regimes(pair, interval, time, regime, adx, ema_slope, realized_vol)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(pair, interval, time) DO UPDATE SET
                 regime=excluded.regime, adx=excluded.adx,
                 ema_slope=excluded.ema_slope, realized_vol=excluded.realized_vol""",
            (pair, interval, time, regime, adx, ema_slope, realized_vol))
        self.conn.commit()

    def latest_regime(self, pair, interval):
        row = self.conn.execute(
            "SELECT regime, adx, ema_slope, realized_vol, time FROM regimes "
            "WHERE pair=? AND interval=? ORDER BY time DESC LIMIT 1", (pair, interval)).fetchone()
        return dict(row) if row else None

    def rebuild_trade_outcomes(self, pair=None, interval=None):
        """Rebuild the denormalized trade_outcomes table from trades (idempotent).

        One row per completed position (its OPEN paired with its final CLOSE). Positions
        with no CLOSE yet are skipped. realized_pnl/fees/slippage sum the position's trades.

        When both `pair` and `interval` are given (this is a single-instrument bot, so the
        caller passes `cfg.pair_candles`/`cfg.interval`), each outcome is also enriched with
        MAE/MFE — the maximum adverse / favorable excursion the trade saw intra-flight,
        measured from the candle path over the holding window and expressed as a signed
        return on entry price (MFE >= 0 favorable, MAE <= 0 adverse). Left NULL when pair/
        interval are absent or no candles cover the window. See `_excursion` for how the
        wall-clock trade timestamps are mapped onto the bar-open candle grid.
        """
        rows = self.conn.execute(
            "SELECT position_id, strategy, side, action, price, size, fee, pnl, ts, slippage, "
            "exit_reason, decision_id, regime_at_entry "
            "FROM trades WHERE position_id IS NOT NULL ORDER BY position_id, id ASC").fetchall()
        by_pos = {}
        for r in rows:
            by_pos.setdefault(r["position_id"], []).append(r)
        self.conn.execute("DELETE FROM trade_outcomes")
        for pos_id, trs in by_pos.items():
            opens = [t for t in trs if t["action"] == "OPEN"]
            closes = [t for t in trs if t["action"] == "CLOSE"]
            if not opens or not closes:
                continue  # not a completed round trip
            o, c = opens[0], closes[-1]
            realized = sum(t["pnl"] for t in trs)
            fees = sum((t["fee"] or 0.0) for t in trs)
            slip = sum((t["slippage"] or 0.0) for t in trs)
            notional = o["price"] * o["size"]
            pnl_pct = (realized / notional * 100) if notional else 0.0
            # prediction_correct: was the directional bet right (mechanically, from prices —
            # never the model's self-assessment)? LONG wants exit > entry; SHORT exit < entry.
            correct = 1 if ((o["side"] == "LONG" and c["price"] > o["price"])
                            or (o["side"] == "SHORT" and c["price"] < o["price"])) else 0
            mae, mfe = self._excursion(pair, interval, o["side"], o["price"], o["ts"], c["ts"])
            self.conn.execute(
                """INSERT INTO trade_outcomes(position_id, strategy, side, entry_price, exit_price,
                       entry_ts, exit_ts, size, fees, slippage, realized_pnl, pnl_pct,
                       holding_period_ms, exit_reason, decision_id, regime_at_entry,
                       mae, mfe, prediction_correct, realized_at_ts)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pos_id, o["strategy"], o["side"], o["price"], c["price"], o["ts"], c["ts"],
                 o["size"], fees, slip, realized, pnl_pct, c["ts"] - o["ts"], c["exit_reason"],
                 o["decision_id"], o["regime_at_entry"], mae, mfe, correct, c["ts"]))
        self.conn.commit()

    def _excursion(self, pair, interval, side, entry_price, entry_ts, exit_ts):
        """MAE/MFE for one trade from the candle high/low path over its holding window.

        Returns (mae, mfe) as signed returns on entry_price: MFE is the best the trade ever
        looked, MAE the worst, both in the trade's own direction. (None, None) when pair/
        interval are missing, entry_price is 0, or no candles cover the window.

        Timestamp mapping: trade ts (entry_ts/exit_ts) is WALL-CLOCK ms (`time.time()` at the
        moment the bar was processed) while candle `time` is BAR-OPEN ms. A naive
        `time >= entry_ts` filter would drop the bar the position actually opened into —
        especially since fetch/processing latency nudges entry_ts just past a bar boundary.
        So we snap the lower bound DOWN to the bar that *contains* entry_ts (the largest
        candle time <= entry_ts) and bound the top at exit_ts. Approximate by construction.
        """
        if not pair or not interval or not entry_price:
            return (None, None)
        anchor = self.conn.execute(
            "SELECT MAX(time) AS t FROM candles WHERE pair=? AND interval=? AND time<=?",
            (pair, interval, entry_ts)).fetchone()
        lo_bound = anchor["t"] if anchor and anchor["t"] is not None else entry_ts
        row = self.conn.execute(
            "SELECT MAX(high) AS hi, MIN(low) AS lo FROM candles "
            "WHERE pair=? AND interval=? AND time>=? AND time<=?",
            (pair, interval, lo_bound, exit_ts)).fetchone()
        if row is None or row["hi"] is None:
            return (None, None)
        hi, lo = row["hi"], row["lo"]
        if side == "SHORT":
            # favorable = price falls; adverse = price rises
            mfe = (entry_price - lo) / entry_price
            mae = (entry_price - hi) / entry_price
        else:  # LONG
            mfe = (hi - entry_price) / entry_price
            mae = (lo - entry_price) / entry_price
        # Excursion is measured from entry (0 at the moment of entry): a favorable excursion
        # can't be negative, an adverse one can't be positive. Clamp so a gapped window that
        # never crossed entry honors the MFE>=0 / MAE<=0 invariant instead of reporting a
        # "favorable" loss.
        return (min(0.0, mae), max(0.0, mfe))

    def trade_outcomes(self, strategy=None, as_of=None):
        """Read trade_outcomes. `as_of` enforces the look-ahead embargo: only rows whose
        realized_at_ts <= as_of are returned (so reflection can't peek at the future)."""
        q = "SELECT * FROM trade_outcomes"
        cond, params = [], []
        if strategy is not None:
            cond.append("strategy=?")
            params.append(strategy)
        if as_of is not None:
            cond.append("realized_at_ts <= ?")
            params.append(as_of)
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY exit_ts ASC"
        return [dict(r) for r in self.conn.execute(q, params)]

    def outcomes_with_confidence(self, strategy=None, as_of=None):
        """trade_outcomes LEFT JOINed to the entry decision's confidence (via decision_id) — the
        join the confidence-calibration report needs (confidence lives on decision_log, not on
        the outcome). `as_of` enforces the look-ahead embargo. decision_confidence is None when
        the outcome has no decision_id or no matching decision row (e.g. a mechanical skill)."""
        q = ("SELECT t.*, d.confidence AS decision_confidence "
             "FROM trade_outcomes t LEFT JOIN decision_log d ON d.decision_id = t.decision_id")
        cond, params = [], []
        if strategy is not None:
            cond.append("t.strategy=?")
            params.append(strategy)
        if as_of is not None:
            cond.append("t.realized_at_ts <= ?")
            params.append(as_of)
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY t.exit_ts ASC"
        return [dict(r) for r in self.conn.execute(q, params)]

    def outcomes_with_playbook(self, strategy=None, as_of=None):
        """trade_outcomes LEFT JOINed to the entry decision's playbook_version (via decision_id) —
        the join per-playbook-version performance + the disconfirmation guard need (the active
        playbook is tagged on the decision, not the outcome). `as_of` enforces the embargo;
        playbook_version is None for trades made on the base prompt (no playbook injected)."""
        q = ("SELECT t.*, d.playbook_version AS playbook_version "
             "FROM trade_outcomes t LEFT JOIN decision_log d ON d.decision_id = t.decision_id")
        cond, params = [], []
        if strategy is not None:
            cond.append("t.strategy=?")
            params.append(strategy)
        if as_of is not None:
            cond.append("t.realized_at_ts <= ?")
            params.append(as_of)
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY t.exit_ts ASC"
        return [dict(r) for r in self.conn.execute(q, params)]

    # --- Phase-4 learn->correct store: reflections + append-only playbooks ---------------
    def record_reflection(self, strategy, kind, ts, *, batch_from_ts=None, batch_to_ts=None,
                          trade_ids=None, metrics=None, lesson=None, new_playbook_version=None,
                          model=None, raw=None) -> int:
        """Persist one reflection (a lesson over a batch of outcomes, or a per-trade critique).
        `trade_ids`/`metrics` are JSON-serialized here. Returns the new reflection id."""
        cur = self.conn.execute(
            """INSERT INTO reflections(strategy, kind, ts, batch_from_ts, batch_to_ts,
                   trade_ids_json, metrics_json, lesson, new_playbook_version, model, raw)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (strategy, kind, ts, batch_from_ts, batch_to_ts,
             json.dumps(trade_ids) if trade_ids is not None else None,
             json.dumps(metrics) if metrics is not None else None,
             lesson, new_playbook_version, model, raw))
        self.conn.commit()
        return cur.lastrowid

    def recent_reflections(self, strategy=None, limit=50, kind=None):
        # Newest by event time (ts), id as a stable tiebreak — uses idx_reflections_strategy_ts
        # for the per-strategy case. `kind` filters periodic vs per_trade so the two loops never
        # read each other's rows (e.g. the periodic watermark must ignore per_trade reflections).
        cond, params = [], []
        if strategy is not None:
            cond.append("strategy=?")
            params.append(strategy)
        if kind is not None:
            cond.append("kind=?")
            params.append(kind)
        q = "SELECT * FROM reflections"
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY ts DESC, id DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(q, params)]

    def get_decision(self, decision_id):
        """The decision_log row for a decision_id (the entry thesis a trade traces back to), or
        None. Read-only join helper for the per-trade Reflexion pass."""
        if not decision_id:
            return None
        row = self.conn.execute(
            "SELECT * FROM decision_log WHERE decision_id=? ORDER BY id ASC LIMIT 1",
            (decision_id,)).fetchone()
        return dict(row) if row else None

    def llm_response_at(self, strategy, ts):
        """The AI response a strategy emitted at decision time `ts` (observation/prediction/
        rationale), or None — decision_log and llm_responses are written with the same wall-clock
        ts in one tick, so an exact ts match recovers the thesis. None for mechanical skills."""
        row = self.conn.execute(
            "SELECT * FROM llm_responses WHERE strategy=? AND ts=? ORDER BY id DESC LIMIT 1",
            (strategy, ts)).fetchone()
        return dict(row) if row else None

    def per_trade_reflection_exists(self, strategy, position_id) -> bool:
        """True if a per_trade reflection already covers this position. The per-trade pass writes
        trade_ids_json = json.dumps([position_id]); an exact match is precise (won't confuse 1
        with 10). Lets the critique be one-per-trade regardless of how its caller is wired."""
        row = self.conn.execute(
            "SELECT 1 FROM reflections WHERE strategy=? AND kind='per_trade' AND trade_ids_json=? "
            "LIMIT 1", (strategy, json.dumps([position_id]))).fetchone()
        return row is not None

    def publish_playbook(self, version, strategy, created_ts, rules, *, parent_version=None):
        """Append a new immutable playbook version. `rules` is JSON-serialized. A published
        version's rules_json is NEVER updated afterwards — refinement supersedes (retire +
        publish a child), it never mutates history."""
        self.conn.execute(
            "INSERT INTO playbooks(version, strategy, created_ts, rules_json, parent_version) "
            "VALUES(?,?,?,?,?)",
            (version, strategy, created_ts, json.dumps(rules), parent_version))
        self.conn.commit()

    def latest_playbook(self, strategy):
        """The current (non-retired) playbook for a strategy, or None."""
        row = self.conn.execute(
            "SELECT * FROM playbooks WHERE strategy=? AND retired_ts IS NULL "
            "ORDER BY created_ts DESC, rowid DESC LIMIT 1", (strategy,)).fetchone()
        return dict(row) if row else None

    def get_playbook(self, version):
        row = self.conn.execute("SELECT * FROM playbooks WHERE version=?", (version,)).fetchone()
        return dict(row) if row else None

    def retire_playbook(self, version, retired_ts) -> None:
        """Mark a version superseded. Only sets retired_ts — never touches rules_json."""
        self.conn.execute("UPDATE playbooks SET retired_ts=? WHERE version=?",
                          (retired_ts, version))
        self.conn.commit()

    # --- Phase-4 proposals: the human-approval gate (nothing self-applies) ---------------
    def create_proposal(self, strategy, kind, payload, rationale, created_ts, *,
                        source_reflection_id=None) -> int:
        """Record a pending proposal. Validates the kind and refuses any payload that touches a
        protected field (risk limits / kill-switch / secrets / live-arming) — raises ValueError.
        Does NOT apply anything; status starts 'pending' until a human decides."""
        if kind not in PROPOSAL_KINDS:
            raise ValueError(f"unknown proposal kind: {kind}")
        # param payloads set config fields, so scan values too (catch field-as-value); other
        # kinds carry free prose in values and are key-scanned only.
        _assert_no_protected_fields(payload, scan_values=(kind == "param"))
        cur = self.conn.execute(
            """INSERT INTO proposals(strategy, kind, payload_json, rationale, status,
                   created_ts, source_reflection_id)
               VALUES(?,?,?,?, 'pending', ?, ?)""",
            (strategy, kind, json.dumps(payload), rationale, created_ts, source_reflection_id))
        self.conn.commit()
        return cur.lastrowid

    def pending_proposals(self, strategy=None):
        if strategy is not None:
            rows = self.conn.execute(
                "SELECT * FROM proposals WHERE status='pending' AND strategy=? "
                "ORDER BY created_ts ASC, id ASC", (strategy,))
        else:
            rows = self.conn.execute(
                "SELECT * FROM proposals WHERE status='pending' ORDER BY created_ts ASC, id ASC")
        return [dict(r) for r in rows]

    def recent_proposals(self, strategy=None, limit=100):
        if strategy is not None:
            rows = self.conn.execute(
                "SELECT * FROM proposals WHERE strategy=? ORDER BY id DESC LIMIT ?",
                (strategy, limit))
        else:
            rows = self.conn.execute(
                "SELECT * FROM proposals ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def get_proposal(self, proposal_id):
        row = self.conn.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        return dict(row) if row else None

    def decide_proposal(self, proposal_id, status, decided_by, decided_ts) -> bool:
        """Approve or reject a PENDING proposal. Only records the decision — applying the change
        is a separate, explicit step. Returns True if a pending row was decided (idempotent: a
        second decision on an already-decided proposal is a no-op)."""
        if status not in ("approved", "rejected"):
            raise ValueError(f"decision must be approved/rejected, got: {status}")
        cur = self.conn.execute(
            "UPDATE proposals SET status=?, decided_by=?, decided_ts=? "
            "WHERE id=? AND status='pending'",
            (status, decided_by, decided_ts, proposal_id))
        self.conn.commit()
        return cur.rowcount > 0

    def apply_playbook_proposal(self, proposal_id, version, strategy, rules, applied_by, now_ms):
        """Apply an approved playbook proposal ATOMICALLY: publish the new immutable version,
        retire the strategy's currently-active version (recording true lineage), and stamp the
        proposal applied — all in ONE transaction. So the change and its provenance are
        all-or-nothing: never a published-but-unmarked version (which a retry would mis-read as
        'already exists') nor a marked-but-unpublished row. Re-verifies inside the txn (under a
        write lock) that the proposal is still approved + un-applied, so a concurrent apply can't
        double it. Returns the published version, or None if the row was no longer applicable.
        Raises sqlite3.IntegrityError only on a duplicate version (PK) — strategy is validated by
        the caller, so this is unambiguous.

        The INSERT/UPDATE shapes here intentionally mirror publish_playbook + retire_playbook +
        the proposal-mark UPDATE; they're inlined (not reused) only because those methods each
        self-commit and can't compose into one transaction. Keep them in sync."""
        prev_isolation = self.conn.isolation_level
        self.conn.isolation_level = None      # honor explicit BEGIN/COMMIT (match _migrate)
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                row = self.conn.execute(
                    "SELECT status, applied_ts FROM proposals WHERE id=?", (proposal_id,)).fetchone()
                if row is None or row["status"] != "approved" or row["applied_ts"] is not None:
                    self.conn.execute("ROLLBACK")
                    return None
                cur = self.conn.execute(
                    "SELECT version FROM playbooks WHERE strategy=? AND retired_ts IS NULL "
                    "ORDER BY created_ts DESC, rowid DESC LIMIT 1", (strategy,)).fetchone()
                parent = cur["version"] if cur else None
                self.conn.execute(
                    "INSERT INTO playbooks(version, strategy, created_ts, rules_json, parent_version) "
                    "VALUES(?,?,?,?,?)",
                    (version, strategy, now_ms, json.dumps({"rules": rules}), parent))
                if parent and parent != version:
                    self.conn.execute("UPDATE playbooks SET retired_ts=? WHERE version=?",
                                      (now_ms, parent))
                self.conn.execute(
                    "UPDATE proposals SET applied_ts=?, applied_by=?, applied_result=? WHERE id=?",
                    (now_ms, applied_by, version, proposal_id))
                self.conn.execute("COMMIT")
                return version
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
        finally:
            self.conn.isolation_level = prev_isolation

    def table_names(self) -> set:
        """Names of all real tables in the live DB (excludes SQLite internals). Used by the
        Hierarchy-of-Truth check to assert every table is classified as audit-truth or
        model-authored."""
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {r["name"] for r in rows}

    def close(self) -> None:
        self.conn.close()
