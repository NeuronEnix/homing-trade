# Phase 2: Backtester + Candle Storage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist all CoinDCX candles (historical backfill + live polling) in SQLite, and add a backtester that replays stored candles through a strategy using the live engine's exact execution path, reporting performance metrics.

**Architecture:** One `candles` table keyed on `(pair, interval, time)` with a `source` column. A gap-aware `history` backfiller fills the DB from CoinDCX only where data is missing. The backtester feeds candles through the existing `engine.process_tick` + `broker.Broker`, swapping the SQLite `Database` for an in-memory `MemoryLedger` with the same method surface, then computes metrics. Backtest + report only — no parameter optimisation.

**Tech Stack:** Python 3.12, stdlib (`sqlite3`, `argparse`, `math`, `time`, `dataclasses`) + `requests` + `pytest`. No pandas/numpy.

## Global Constraints

- Python 3.12. stdlib + `requests` + `pytest` only (no pandas/numpy).
- Currency INR; paper only — no API keys, no live orders.
- Backtester MUST reuse `engine.process_tick` and `broker.Broker` unchanged — no reimplementation of trade logic.
- Candle store: ONE table `candles`, primary key `(pair, interval, time)`, `source` in `{'history','live'}`. Upsert on conflict (`DO UPDATE`).
- DB file `data/paper_trading.db`. Never commit `data/` or `*.db`.
- `now_ms` is always passed in to history functions (never read inside) so tests are deterministic.
- All backtests are in-memory and must NOT write to the strategy/wallet/trade/equity tables.
- Commit after each task; run tests before each commit. Every commit message ends with the trailer:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- Run tests via the project venv: `cd /Users/krb/adoc2/rnd/algo-trading && ./.venv/bin/python -m pytest <path> -v`

---

### Task 1: Candle storage in SQLite

**Files:**
- Modify: `algotrading/db.py` (add `candles` table to `SCHEMA`; import `Candle`; add 4 methods)
- Test: `tests/test_db_candles.py`

**Interfaces:**
- Consumes: `models.Candle`.
- Produces (on `Database`):
  - `save_candles(pair: str, interval: str, candles: list[Candle], source: str) -> int` — upserts; returns number written.
  - `get_candles_range(pair, interval, start_ms, end_ms, source: str = "all") -> list[Candle]` — inclusive range, ascending by time; `source` "all" returns both.
  - `get_candle_bounds(pair, interval) -> tuple[int, int] | None` — `(min_time, max_time)` or None.
  - `count_candles(pair, interval, source: str = "all") -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db_candles.py
from algotrading.db import Database
from algotrading.models import Candle


def mk(time, close, src_close=None):
    return Candle(open=close, high=close + 1, low=close - 1, close=close, volume=1.0, time=time)


def db(tmp_path):
    return Database(str(tmp_path / "c.db"))


def test_save_and_range(tmp_path):
    d = db(tmp_path)
    d.save_candles("I-BTC_INR", "1m", [mk(2000, 101), mk(1000, 100), mk(3000, 102)], "history")
    got = d.get_candles_range("I-BTC_INR", "1m", 1000, 3000)
    assert [c.time for c in got] == [1000, 2000, 3000]  # ascending
    assert got[0].close == 100


def test_upsert_dedupe_updates_values(tmp_path):
    d = db(tmp_path)
    d.save_candles("I-BTC_INR", "1m", [mk(1000, 100)], "history")
    d.save_candles("I-BTC_INR", "1m", [mk(1000, 999)], "live")  # same time -> update, no dup
    got = d.get_candles_range("I-BTC_INR", "1m", 0, 5000)
    assert len(got) == 1
    assert got[0].close == 999
    assert d.count_candles("I-BTC_INR", "1m", source="live") == 1
    assert d.count_candles("I-BTC_INR", "1m", source="history") == 0


def test_range_source_filter(tmp_path):
    d = db(tmp_path)
    d.save_candles("I-BTC_INR", "1m", [mk(1000, 100)], "history")
    d.save_candles("I-BTC_INR", "1m", [mk(2000, 101)], "live")
    assert [c.time for c in d.get_candles_range("I-BTC_INR", "1m", 0, 9000, "live")] == [2000]
    assert [c.time for c in d.get_candles_range("I-BTC_INR", "1m", 0, 9000, "history")] == [1000]
    assert len(d.get_candles_range("I-BTC_INR", "1m", 0, 9000, "all")) == 2


def test_bounds_and_count(tmp_path):
    d = db(tmp_path)
    assert d.get_candle_bounds("I-BTC_INR", "1m") is None
    d.save_candles("I-BTC_INR", "1m", [mk(1000, 100), mk(3000, 102)], "history")
    assert d.get_candle_bounds("I-BTC_INR", "1m") == (1000, 3000)
    assert d.count_candles("I-BTC_INR", "1m") == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_db_candles.py -v`
Expected: FAIL (`AttributeError: 'Database' object has no attribute 'save_candles'`)

- [ ] **Step 3: Implement**

In `algotrading/db.py`, change the import line:

```python
from algotrading.models import Candle, Position
```

Append this table to the `SCHEMA` string (before the closing `"""`):

```sql
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
```

Add these methods to the `Database` class:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_db_candles.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Confirm nothing else broke + commit**

Run: `./.venv/bin/python -m pytest -q` → all pass.

```bash
git add algotrading/db.py tests/test_db_candles.py
git commit -m "feat: persistent candle storage in SQLite (history + live, one table)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: MemoryLedger (in-memory Database stand-in)

**Files:**
- Create: `algotrading/ledger.py`
- Test: `tests/test_ledger.py`

**Interfaces:**
- Consumes: `models.Position`.
- Produces: `MemoryLedger(strategy: str, starting_balance: float)` implementing the exact method surface `engine.process_tick` and its helpers call on `Database`: `ensure_strategy`, `get_balance`, `set_balance`, `open_position`, `close_position`, `get_open_position`, `record_trade`, `record_equity`, `log_decision`. Exposes `self.trades: list[dict]` and `self.equity_curve: list[tuple[int, float]]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ledger.py
from algotrading.ledger import MemoryLedger
from algotrading.models import Position


def test_balance_get_set():
    led = MemoryLedger("ma_trend", 5000.0)
    assert led.get_balance("ma_trend") == 5000.0
    led.set_balance("ma_trend", 4800.0)
    assert led.get_balance("ma_trend") == 4800.0


def test_open_get_close_position():
    led = MemoryLedger("ma_trend", 5000.0)
    pos = Position(strategy="ma_trend", side="LONG", entry_price=100.0, size=1.0,
                   leverage=3.0, margin=33.0, stop_price=98.0, opened_at=1000)
    pid = led.open_position(pos)
    assert pid == 1
    fetched = led.get_open_position("ma_trend")
    assert fetched is not None and fetched.id == 1 and fetched.side == "LONG"
    led.close_position(pid)
    assert led.get_open_position("ma_trend") is None


def test_records_accumulate():
    led = MemoryLedger("ma_trend", 5000.0)
    led.record_trade("ma_trend", 1, "LONG", "OPEN", 100.0, 1.0, 0.05, -0.05, 1000)
    led.record_trade("ma_trend", 1, "LONG", "CLOSE", 110.0, 1.0, 0.05, 9.9, 2000)
    led.record_equity("ma_trend", 5009.9, 2000)
    led.log_decision("ma_trend", 2000, 2000, "CLOSE", 0.6, "x", {"rsi": 71})
    assert len(led.trades) == 2
    assert led.trades[1]["action"] == "CLOSE"
    assert led.equity_curve == [(2000, 5009.9)]


def test_ensure_strategy_idempotent():
    led = MemoryLedger("ma_trend", 5000.0)
    led.ensure_strategy("ma_trend", 5000.0)  # must not reset
    led.set_balance("ma_trend", 100.0)
    led.ensure_strategy("ma_trend", 5000.0)
    assert led.get_balance("ma_trend") == 100.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_ledger.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'algotrading.ledger'`)

- [ ] **Step 3: Implement**

```python
# algotrading/ledger.py
from algotrading.models import Position


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_ledger.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add algotrading/ledger.py tests/test_ledger.py
git commit -m "feat: MemoryLedger — in-memory Database stand-in for backtesting

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Metrics

**Files:**
- Create: `algotrading/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces: `CANDLE_INTERVAL_MS: dict[str, int]`; `periods_per_year(interval) -> float`;
  `total_return_pct(start_balance, final_equity) -> float`; `win_rate(trades) -> float`;
  `profit_factor(trades) -> float`; `avg_win(trades) -> float`; `avg_loss(trades) -> float`;
  `max_drawdown(equity_curve) -> float`; `sharpe(equity_curve, periods_per_year_value) -> float`.
  `trades` is a list of dicts with `action` and `pnl` keys; `equity_curve` is a list of `(ts, equity)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metrics.py
import math
from algotrading import metrics


def closes(*pnls):
    return [{"action": "CLOSE", "pnl": p} for p in pnls]


def test_periods_per_year_1m():
    assert metrics.periods_per_year("1m") == 525600.0


def test_total_return_pct():
    assert metrics.total_return_pct(5000.0, 5100.0) == 2.0
    assert metrics.total_return_pct(0.0, 100.0) == 0.0


def test_win_rate():
    assert metrics.win_rate(closes(10, -5, 20)) == 2 / 3
    assert metrics.win_rate([]) == 0.0


def test_profit_factor():
    assert metrics.profit_factor(closes(10, 20, -5)) == 30 / 5
    assert metrics.profit_factor(closes(10, 20)) == float("inf")
    assert metrics.profit_factor([]) == 0.0


def test_avg_win_loss():
    assert metrics.avg_win(closes(10, 20, -6)) == 15.0
    assert metrics.avg_loss(closes(10, -4, -6)) == -5.0


def test_max_drawdown():
    curve = [(0, 100.0), (1, 120.0), (2, 90.0), (3, 150.0)]
    assert metrics.max_drawdown(curve) == (120.0 - 90.0) / 120.0  # 0.25


def test_sharpe_zero_variance_returns_zero():
    # constant 10% step -> identical returns -> zero variance -> 0.0
    assert metrics.sharpe([(0, 100.0), (1, 110.0), (2, 121.0)], 1.0) == 0.0


def test_sharpe_positive_for_varied_growth():
    assert metrics.sharpe([(0, 100.0), (1, 110.0), (2, 120.0)], 1.0) > 0


def test_sharpe_too_few_points():
    assert metrics.sharpe([(0, 100.0)], 525600.0) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_metrics.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'algotrading.metrics'`)

- [ ] **Step 3: Implement**

```python
# algotrading/metrics.py
import math

CANDLE_INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "8h": 28_800_000, "1d": 86_400_000, "3d": 259_200_000, "1w": 604_800_000,
}

_MS_PER_YEAR = 365 * 86_400_000


def periods_per_year(interval: str) -> float:
    return _MS_PER_YEAR / CANDLE_INTERVAL_MS[interval]


def _closed(trades):
    return [t for t in trades if t["action"] == "CLOSE"]


def total_return_pct(start_balance: float, final_equity: float) -> float:
    if start_balance == 0:
        return 0.0
    return (final_equity - start_balance) / start_balance * 100


def win_rate(trades) -> float:
    closed = _closed(trades)
    if not closed:
        return 0.0
    return sum(1 for t in closed if t["pnl"] > 0) / len(closed)


def profit_factor(trades) -> float:
    closed = _closed(trades)
    gross_profit = sum(t["pnl"] for t in closed if t["pnl"] > 0)
    gross_loss = -sum(t["pnl"] for t in closed if t["pnl"] < 0)
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def avg_win(trades) -> float:
    wins = [t["pnl"] for t in _closed(trades) if t["pnl"] > 0]
    return sum(wins) / len(wins) if wins else 0.0


def avg_loss(trades) -> float:
    losses = [t["pnl"] for t in _closed(trades) if t["pnl"] < 0]
    return sum(losses) / len(losses) if losses else 0.0


def max_drawdown(equity_curve) -> float:
    peak = None
    max_dd = 0.0
    for _, eq in equity_curve:
        if peak is None or eq > peak:
            peak = eq
        if peak and peak > 0:
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def sharpe(equity_curve, periods_per_year_value: float) -> float:
    eqs = [eq for _, eq in equity_curve]
    if len(eqs) < 2:
        return 0.0
    rets = []
    for i in range(1, len(eqs)):
        prev = eqs[i - 1]
        if prev != 0:
            rets.append((eqs[i] - prev) / prev)
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(periods_per_year_value)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_metrics.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add algotrading/metrics.py tests/test_metrics.py
git commit -m "feat: backtest metrics (return, Sharpe, drawdown, profit factor, win rate)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: History backfill (gap-aware, cached)

**Files:**
- Create: `algotrading/history.py`
- Test: `tests/test_history.py`

**Interfaces:**
- Consumes: `feed.parse_candles`, `feed.CANDLES_URL`, `feed._http_fetcher`, `metrics.CANDLE_INTERVAL_MS`, and the `Database` candle methods from Task 1.
- Produces: `ensure_history(db, pair, interval, days, now_ms, *, fetcher=None) -> list[Candle]`.
  Fetches only missing spans (paging ≤1000 candles via `startTime`/`endTime`/`limit`), saves with `source="history"`, returns `db.get_candles_range(...)` for the aligned range. `fetcher(url, params) -> list[dict]` is injectable. Range is aligned to interval boundaries; `end` is the last completed candle.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_history.py
from algotrading.db import Database
from algotrading.history import ensure_history

STEP = 3_600_000  # 1h in ms
NOW = 1000 * STEP  # interval-aligned "now"


def make_fetcher(calls):
    # returns candles at STEP intervals within [startTime, min(endTime, startTime+STEP*(limit-1))]
    def fetcher(url, params):
        calls.append(params)
        t = params["startTime"]
        end = params["endTime"]
        limit = params["limit"]
        out = []
        while t <= end and len(out) < limit:
            out.append({"open": 100.0, "high": 101.0, "low": 99.0,
                        "close": 100.0, "volume": 1.0, "time": t})
            t += STEP
        return out
    return fetcher


def test_first_call_fetches_and_stores(tmp_path):
    d = Database(str(tmp_path / "h.db"))
    calls = []
    out = ensure_history(d, "I-BTC_INR", "1h", 1, NOW, fetcher=make_fetcher(calls))
    assert len(calls) >= 1
    assert len(out) > 0
    assert d.count_candles("I-BTC_INR", "1h", source="history") == len(out)


def test_second_call_makes_no_fetch(tmp_path):
    d = Database(str(tmp_path / "h.db"))
    ensure_history(d, "I-BTC_INR", "1h", 1, NOW, fetcher=make_fetcher([]))

    def boom(url, params):
        raise AssertionError("should not fetch when data already present")

    out = ensure_history(d, "I-BTC_INR", "1h", 1, NOW, fetcher=boom)  # must not raise
    assert len(out) > 0


def test_gap_fill_only_fetches_missing(tmp_path):
    d = Database(str(tmp_path / "h.db"))
    from algotrading.models import Candle
    # Pre-store a middle band [NOW-20h .. NOW-10h]
    mid = [Candle(open=100, high=101, low=99, close=100, volume=1, time=NOW - k * STEP)
           for k in range(10, 21)]
    d.save_candles("I-BTC_INR", "1h", mid, "history")
    calls = []
    ensure_history(d, "I-BTC_INR", "1h", 1, NOW, fetcher=make_fetcher(calls))
    # missing older span (< band) and newer span (> band) -> at least 2 fetch calls
    assert len(calls) >= 2


def test_fetch_error_returns_stored_without_raising(tmp_path):
    d = Database(str(tmp_path / "h.db"))

    def boom(url, params):
        raise RuntimeError("network down")

    out = ensure_history(d, "I-BTC_INR", "1h", 1, NOW, fetcher=boom)
    assert out == []  # nothing stored, nothing raised
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_history.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'algotrading.history'`)

- [ ] **Step 3: Implement**

```python
# algotrading/history.py
from algotrading.feed import parse_candles, CANDLES_URL, _http_fetcher
from algotrading.metrics import CANDLE_INTERVAL_MS

_DAY_MS = 86_400_000
_MAX_LIMIT = 1000


def ensure_history(db, pair, interval, days, now_ms, *, fetcher=None):
    fetcher = fetcher or _http_fetcher
    step = CANDLE_INTERVAL_MS[interval]
    end = (now_ms // step) * step - step                 # last completed candle, aligned
    start = ((now_ms - days * _DAY_MS) // step) * step    # aligned range start
    bounds = db.get_candle_bounds(pair, interval)
    if bounds is None:
        spans = [(start, end)]
    else:
        mn, mx = bounds
        spans = []
        if start < mn:
            spans.append((start, mn - step))
        if end > mx:
            spans.append((mx + step, end))
    for span_start, span_end in spans:
        _fetch_span(db, pair, interval, span_start, span_end, step, fetcher)
    return db.get_candles_range(pair, interval, start, end, source="all")


def _fetch_span(db, pair, interval, span_start, span_end, step, fetcher):
    cursor = span_start
    while cursor <= span_end:
        chunk_end = min(cursor + step * (_MAX_LIMIT - 1), span_end)
        params = {"pair": pair, "interval": interval,
                  "startTime": cursor, "endTime": chunk_end, "limit": _MAX_LIMIT}
        try:
            raw = fetcher(CANDLES_URL, params)
        except Exception as exc:
            print(f"[history] fetch failed at {cursor}, keeping stored data: {exc}")
            return
        candles = parse_candles(raw)
        if not candles:
            break
        db.save_candles(pair, interval, candles, source="history")
        last_time = candles[-1].time
        if last_time < cursor:  # no forward progress; stop to avoid an infinite loop
            break
        cursor = last_time + step
```

Note: gap detection assumes the stored range `[mn, mx]` is contiguous (true for our backfill + live append usage). Internal holes are out of scope for Phase 2.

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_history.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add algotrading/history.py tests/test_history.py
git commit -m "feat: gap-aware history backfill with SQLite caching

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Capture live candles in the engine

**Files:**
- Modify: `algotrading/engine.py` (in `run()`, persist fetched candles with `source="live"`)
- Test: `tests/test_engine.py` (add one test)

**Interfaces:**
- Consumes: `Database.save_candles` (Task 1).
- Produces: no new public symbol; `run()` now calls `db.save_candles(cfg.pair_candles, cfg.interval, candles, source="live")` once per successful fetch (when `candles` is non-empty), before the cursor check.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_engine.py`:

```python
def test_run_persists_live_candles(tmp_path):
    cfg = Config(db_path=str(tmp_path / "live.db"))
    candles_raw = [{"open": c.open, "high": c.high, "low": c.low, "close": c.close,
                    "volume": c.volume, "time": c.time} for c in rising_then_drop()]

    def fake_fetcher(url, params):
        return candles_raw

    run(cfg, fetcher=fake_fetcher, max_ticks=1, sleeper=lambda s: None)
    db = Database(cfg.db_path)
    n = db.count_candles(cfg.pair_candles, cfg.interval, source="live")
    assert n == len(candles_raw)
    db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_engine.py::test_run_persists_live_candles -v`
Expected: FAIL (count is 0 — candles not yet persisted)

- [ ] **Step 3: Implement**

In `algotrading/engine.py`, inside `run()`, locate the block:

```python
            if candles:
                newest = str(candles[-1].time)
```

Insert a `save_candles` call as the first line inside `if candles:`:

```python
            if candles:
                db.save_candles(cfg.pair_candles, cfg.interval, candles, source="live")
                newest = str(candles[-1].time)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_engine.py -v`
Expected: PASS (all engine tests, including the new one)

- [ ] **Step 5: Confirm full suite + commit**

Run: `./.venv/bin/python -m pytest -q` → all pass.

```bash
git add algotrading/engine.py tests/test_engine.py
git commit -m "feat: persist live candles to SQLite each engine tick

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Backtester + CLI

**Files:**
- Create: `algotrading/backtest.py`
- Test: `tests/test_backtest.py`

**Interfaces:**
- Consumes: `config.CONFIG`/`Config`, `db.Database`, `broker.Broker`, `engine.build_skills`,
  `engine.process_tick`, `ledger.MemoryLedger`, `history.ensure_history`, `metrics`.
- Produces:
  - `run_backtest(skill, candles, cfg, starting_balance, window=200) -> dict` with keys
    `strategy, trades, final_equity, return_pct, win_rate, profit_factor, max_drawdown,
    sharpe, avg_win, avg_loss, equity_curve`.
  - `main(argv=None, cfg=CONFIG)` — argparse CLI.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest.py
from algotrading.backtest import run_backtest
from algotrading.config import CONFIG
from algotrading.skills.ma_trend import MaTrend
from algotrading.models import Candle


def candles_from(prices):
    return [Candle(open=p, high=p + 1, low=p - 1, close=p, volume=1.0, time=1000 + i * 60000)
            for i, p in enumerate(prices)]


def test_run_backtest_returns_expected_keys():
    candles = candles_from([float(x) for x in range(1, 130)])
    r = run_backtest(MaTrend(), candles, CONFIG, 5000.0)
    for k in ("strategy", "trades", "final_equity", "return_pct", "win_rate",
              "profit_factor", "max_drawdown", "sharpe", "avg_win", "avg_loss",
              "equity_curve"):
        assert k in r
    assert r["strategy"] == "ma_trend"
    assert len(r["equity_curve"]) > 0


def test_run_backtest_deterministic_values():
    prices = [float(x) for x in list(range(50, 10, -1)) + list(range(10, 50)) + list(range(50, 10, -1))]
    candles = candles_from(prices)
    r1 = run_backtest(MaTrend(), candles, CONFIG, 5000.0)
    r2 = run_backtest(MaTrend(), candles, CONFIG, 5000.0)
    assert r1["return_pct"] == r2["return_pct"]
    assert r1["trades"] == r2["trades"]
    # equity VALUES are deterministic (timestamps may differ run-to-run)
    assert [e for _, e in r1["equity_curve"]] == [e for _, e in r2["equity_curve"]]


def test_run_backtest_flat_series_no_trades():
    candles = candles_from([100.0] * 130)
    r = run_backtest(MaTrend(), candles, CONFIG, 5000.0)
    assert r["trades"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_backtest.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'algotrading.backtest'`)

- [ ] **Step 3: Implement**

```python
# algotrading/backtest.py
import argparse
import time
from dataclasses import replace
from algotrading.config import CONFIG
from algotrading.db import Database
from algotrading.broker import Broker
from algotrading.engine import build_skills, process_tick
from algotrading.ledger import MemoryLedger
from algotrading.history import ensure_history
from algotrading import metrics

_DAY_MS = 86_400_000


def run_backtest(skill, candles, cfg, starting_balance, window=200):
    ledger = MemoryLedger(skill.name, starting_balance)
    broker = Broker(cfg.fee, cfg.slippage)
    n = len(candles)
    for i in range(1, n + 1):
        win = candles[max(0, i - window):i]
        process_tick(ledger, broker, [skill], win, cfg)
    final_equity = ledger.equity_curve[-1][1] if ledger.equity_curve else starting_balance
    ppy = metrics.periods_per_year(cfg.interval)
    return {
        "strategy": skill.name,
        "trades": len([t for t in ledger.trades if t["action"] == "CLOSE"]),
        "final_equity": final_equity,
        "return_pct": metrics.total_return_pct(starting_balance, final_equity),
        "win_rate": metrics.win_rate(ledger.trades),
        "profit_factor": metrics.profit_factor(ledger.trades),
        "max_drawdown": metrics.max_drawdown(ledger.equity_curve),
        "sharpe": metrics.sharpe(ledger.equity_curve, ppy),
        "avg_win": metrics.avg_win(ledger.trades),
        "avg_loss": metrics.avg_loss(ledger.trades),
        "equity_curve": ledger.equity_curve,
    }


def _format(results):
    header = (f"{'strategy':<12} {'trades':>7} {'return%':>9} {'sharpe':>8} "
              f"{'maxDD%':>7} {'win%':>6} {'PF':>7}")
    lines = [header, "-" * len(header)]
    for r in sorted(results, key=lambda x: x["return_pct"], reverse=True):
        pf = r["profit_factor"]
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        lines.append(f"{r['strategy']:<12} {r['trades']:>7} {r['return_pct']:>8.2f}% "
                     f"{r['sharpe']:>8.2f} {r['max_drawdown'] * 100:>6.2f}% "
                     f"{r['win_rate'] * 100:>5.1f}% {pf_s:>7}")
    return "\n".join(lines)


def _write_csv(path, results):
    with open(path, "w") as f:
        f.write("time,strategy,equity\n")
        for r in results:
            for ts, eq in r["equity_curve"]:
                f.write(f"{ts},{r['strategy']},{eq}\n")


def main(argv=None, cfg=CONFIG):
    parser = argparse.ArgumentParser(
        description="Backtest paper-trading strategies on stored CoinDCX candles.")
    parser.add_argument("--skill", default=None, help="strategy name (default: all enabled)")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--interval", default=cfg.interval)
    parser.add_argument("--source", choices=["all", "live", "history"], default="all")
    parser.add_argument("--balance", type=float, default=cfg.starting_balance)
    parser.add_argument("--csv", default=None)
    args = parser.parse_args(argv)

    names = [args.skill] if args.skill else list(cfg.enabled_skills)
    skills = build_skills(names)
    run_cfg = replace(cfg, interval=args.interval)

    db = Database(cfg.db_path)
    try:
        now_ms = int(time.time() * 1000)
        ensure_history(db, cfg.pair_candles, args.interval, args.days, now_ms)
        start = (now_ms - args.days * _DAY_MS)
        candles = db.get_candles_range(cfg.pair_candles, args.interval, start, now_ms,
                                       source=args.source)
        results = [run_backtest(s, candles, run_cfg, args.balance) for s in skills]
        print(f"Backtest: {cfg.pair_candles} {args.interval}  last {args.days}d  "
              f"source={args.source}  candles={len(candles)}")
        print(_format(results))
        print("Note: backtest results can overfit — confirm with live paper "
              "trading before going live.")
        if args.csv:
            _write_csv(args.csv, results)
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_backtest.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Full suite + LIVE smoke test, then commit**

Run: `./.venv/bin/python -m pytest -q` → all pass.

LIVE smoke test (one real, read-only CoinDCX backfill — allowed; small range for speed):

```bash
./.venv/bin/python -m algotrading.backtest --days 2 --interval 1h
```
Expected: backfills ~48 candles, prints a leaderboard table for the 3 strategies + the overfit reminder. Run it a second time and confirm it is faster / makes no new fetch (cached). Then confirm no db/data is staged: `git status --short` must show only `algotrading/backtest.py` and `tests/test_backtest.py`.

```bash
git add algotrading/backtest.py tests/test_backtest.py
git commit -m "feat: backtester + CLI over stored candles, reusing live execution path

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Candle storage table + methods (spec §3) → Task 1. ✅
- Capture live candles in engine (spec §4) → Task 5. ✅
- History backfill, gap-aware, cached, paged, error-safe (spec §5) → Task 4. ✅
- MemoryLedger matching process_tick's surface (spec §6) → Task 2. ✅
- Metrics incl. periods_per_year/Sharpe/drawdown/PF (spec §7) → Task 3. ✅
- Backtester reusing process_tick + CLI with --skill/--days/--interval/--source/--balance/--csv (spec §8) → Task 6. ✅
- Report table + overfit reminder (spec §9) → Task 6 `_format`/`main`. ✅
- Reuse process_tick/Broker unchanged (spec §2) → Tasks 2/6 (no engine logic rewrite; Task 5 is additive). ✅
- Testing approach (spec §11): metrics unit, ledger round-trip, candle upsert/range/source, history fetch-once/gap/error, backtest determinism/flat, engine live-persist → covered. ✅

**Placeholder scan:** No TBD/TODO; every code step has full code. ✅

**Type consistency:** `MemoryLedger` method signatures match the calls in `engine.process_tick`/`_open_position`/`_close_position` (record_trade 9 args, record_equity 3, log_decision variadic, get/set_balance, open/close/get_open_position). `run_backtest` returns the dict keys the CLI `_format` reads. `CANDLE_INTERVAL_MS` defined in `metrics` (Task 3) and imported by `history` (Task 4). `Database.save_candles`/`get_candles_range`/`get_candle_bounds`/`count_candles` used identically in Tasks 4, 5, 6. ✅
