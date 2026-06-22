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
SCHEMA_VERSION = 1

# Each migration is a LIST of single SQL statements (no trailing ';'). _migrate() applies all
# statements of a version PLUS its schema_version bump inside one transaction, so a failure
# rolls the whole version back rather than leaving the DB half-migrated. Keep statements
# idempotent (IF NOT EXISTS) anyway as belt-and-braces.
MIGRATIONS = {
    # v1: indexes that make the reflection / leaderboard joins cheap (Phase 2 reads these hot).
    1: [
        "CREATE INDEX IF NOT EXISTS idx_decision_log_strategy_ts ON decision_log(strategy, ts)",
        "CREATE INDEX IF NOT EXISTS idx_llm_responses_strategy_ts ON llm_responses(strategy, ts)",
        "CREATE INDEX IF NOT EXISTS idx_trades_strategy_ts ON trades(strategy, ts)",
        "CREATE INDEX IF NOT EXISTS idx_trades_position_id ON trades(position_id)",
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

    def record_trade(self, strategy, position_id, side, action, price, size, fee, pnl, ts) -> None:
        self.conn.execute(
            """INSERT INTO trades(strategy, position_id, side, action, price, size, fee, pnl, ts)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (strategy, position_id, side, action, price, size, fee, pnl, ts),
        )
        self.conn.commit()

    def record_equity(self, strategy, equity, ts) -> None:
        self.conn.execute("INSERT INTO equity(strategy, equity, ts) VALUES(?,?,?)",
                          (strategy, equity, ts))
        self.conn.commit()

    def log_decision(self, strategy, ts, candle_time, action, confidence, reason, indicators) -> None:
        self.conn.execute(
            """INSERT INTO decision_log(strategy, ts, candle_time, action, confidence, reason, indicators_json)
               VALUES(?,?,?,?,?,?,?)""",
            (strategy, ts, candle_time, action, confidence, reason, json.dumps(indicators)),
        )
        self.conn.commit()

    def record_llm_response(self, strategy, ts, backend, model, action, confidence,
                            observation, prediction, rationale, raw, error) -> None:
        self.conn.execute(
            """INSERT INTO llm_responses(strategy, ts, backend, model, action, confidence,
                                         observation, prediction, rationale, raw, error)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (strategy, ts, backend, model, action, confidence,
             observation, prediction, rationale, raw, error),
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

    def close(self) -> None:
        self.conn.close()
