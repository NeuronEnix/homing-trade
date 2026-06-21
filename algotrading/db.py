import json
import os
import sqlite3
from algotrading.models import Candle, Position

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


class Database:
    def __init__(self, path: str):
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

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

    def close(self) -> None:
        self.conn.close()
