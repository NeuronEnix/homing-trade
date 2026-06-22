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
SCHEMA_VERSION = 6

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
}


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

    def close(self) -> None:
        self.conn.close()
