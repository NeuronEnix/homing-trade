# Phase 1: Core Strategy Lab — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A paper-trading tournament where 3 rule-based strategies each trade an isolated virtual ₹5,000 wallet against live CoinDCX BTC/INR candles, with all activity persisted to SQLite and a CLI leaderboard.

**Architecture:** Small single-responsibility modules under `algotrading/`. Pure-function indicators and strategies (easy TDD), a pure-ish `Broker` for paper execution math, a `db` layer wrapping stdlib `sqlite3`, and an `engine` loop that wires feed → skills → broker → db. Strategies implement one `Strategy.on_candle()` interface so ML/AI skills can be added later without rearchitecting.

**Tech Stack:** Python 3.12, `requests`, `pytest`. Indicators in plain Python (no pandas/numpy yet). SQLite via stdlib `sqlite3`.

## Global Constraints

- Python 3.12 (already installed via asdf at `/Users/krb/.asdf/installs/python/3.12.13`).
- Currency is **INR (₹)** everywhere — wallets, P&L, reports.
- **Paper only.** No API keys, no authenticated endpoints, no live orders in Phase 1.
- Only public CoinDCX endpoints: candles `https://public.coindcx.com/market_data/candles`, ticker `https://api.coindcx.com/exchange/ticker`.
- Default pair: candles `I-BTC_INR`, ticker market `BTCINR`, interval `1m`.
- Per-strategy starting balance: **5000** INR. Leverage **3**. Fee **0.0005** (0.05%). Slippage **0.0005** (5 bps). risk_pct **0.02**. stop_pct **0.02**.
- DB file: `data/paper_trading.db` (gitignored). Never commit the db or any data files.
- Commit after every task. Run tests before each commit.
- All commits end with the trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

### Task 1: Project scaffold — package, requirements, config, shared models

**Files:**
- Create: `requirements.txt`
- Create: `algotrading/__init__.py`
- Create: `algotrading/config.py`
- Create: `algotrading/models.py`
- Create: `algotrading/skills/__init__.py`
- Create: `tests/__init__.py`
- Test: `tests/test_models_config.py`

**Interfaces:**
- Produces: `models.Candle(open,high,low,close,volume,time)`, `models.Signal(action,confidence,reason,indicators)`, `models.Position(strategy,side,entry_price,size,leverage,margin,stop_price,opened_at,id,status)`. `config.CONFIG` (a `Config` dataclass instance) with fields: `pair_candles:str`, `ticker_market:str`, `interval:str`, `poll_seconds:int`, `starting_balance:float`, `leverage:float`, `fee:float`, `slippage:float`, `risk_pct:float`, `stop_pct:float`, `db_path:str`, `enabled_skills:list[str]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models_config.py
from algotrading.models import Candle, Signal, Position
from algotrading.config import CONFIG


def test_candle_fields():
    c = Candle(open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0, time=1000)
    assert c.close == 1.5 and c.time == 1000


def test_signal_defaults():
    s = Signal(action="LONG")
    assert s.action == "LONG"
    assert s.confidence == 0.0
    assert s.reason == ""
    assert s.indicators == {}


def test_position_defaults():
    p = Position(strategy="ma_trend", side="LONG", entry_price=100.0, size=0.5,
                 leverage=3.0, margin=50.0, stop_price=98.0, opened_at=1000)
    assert p.id is None and p.status == "open"


def test_config_values():
    assert CONFIG.starting_balance == 5000.0
    assert CONFIG.leverage == 3.0
    assert CONFIG.fee == 0.0005
    assert CONFIG.pair_candles == "I-BTC_INR"
    assert "ma_trend" in CONFIG.enabled_skills
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/krb/adoc2/rnd/algo-trading && python -m pytest tests/test_models_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'algotrading'`

- [ ] **Step 3: Create the files**

```text
# requirements.txt
requests>=2.31
pytest>=8.0
```

```python
# algotrading/__init__.py
```

```python
# algotrading/skills/__init__.py
```

```python
# tests/__init__.py
```

```python
# algotrading/models.py
from dataclasses import dataclass, field


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: float
    time: int  # epoch milliseconds


@dataclass
class Signal:
    action: str            # "LONG" | "SHORT" | "CLOSE" | "HOLD"
    confidence: float = 0.0
    reason: str = ""
    indicators: dict = field(default_factory=dict)


@dataclass
class Position:
    strategy: str
    side: str              # "LONG" | "SHORT"
    entry_price: float
    size: float            # units of base asset (BTC)
    leverage: float
    margin: float          # INR margin locked
    stop_price: float
    opened_at: int         # epoch ms
    id: int | None = None
    status: str = "open"   # "open" | "closed"
```

```python
# algotrading/config.py
from dataclasses import dataclass, field


@dataclass
class Config:
    pair_candles: str = "I-BTC_INR"
    ticker_market: str = "BTCINR"
    interval: str = "1m"
    poll_seconds: int = 60
    starting_balance: float = 5000.0
    leverage: float = 3.0
    fee: float = 0.0005       # 0.05% per side
    slippage: float = 0.0005  # 5 bps
    risk_pct: float = 0.02    # max loss fraction of balance per trade
    stop_pct: float = 0.02    # stop distance as fraction of entry price
    db_path: str = "data/paper_trading.db"
    enabled_skills: list[str] = field(
        default_factory=lambda: ["ma_trend", "rsi_revert", "grid"]
    )


CONFIG = Config()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_models_config.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add requirements.txt algotrading/ tests/
git commit -m "feat: project scaffold, config, and shared models

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Indicators (EMA, RSI)

**Files:**
- Create: `algotrading/skills/indicators.py`
- Test: `tests/test_indicators.py`

**Interfaces:**
- Produces: `ema(values: list[float], period: int) -> float | None`, `rsi(values: list[float], period: int = 14) -> float | None`. Both return `None` when there is insufficient data.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_indicators.py
from algotrading.skills.indicators import ema, rsi


def test_ema_insufficient_data_returns_none():
    assert ema([1, 2], 5) is None


def test_ema_constant_series_equals_value():
    assert ema([10.0] * 20, 5) == 10.0


def test_ema_recent_weighting():
    # rising series -> EMA below the last value but above the mean
    series = [float(i) for i in range(1, 21)]  # 1..20
    val = ema(series, 5)
    assert 17.0 < val < 20.0


def test_rsi_insufficient_data_returns_none():
    assert rsi([1, 2, 3], 14) is None


def test_rsi_all_gains_is_100():
    series = [float(i) for i in range(1, 30)]  # strictly increasing
    assert rsi(series, 14) == 100.0


def test_rsi_all_losses_is_low():
    series = [float(i) for i in range(30, 1, -1)]  # strictly decreasing
    assert rsi(series, 14) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_indicators.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError: cannot import name 'ema'`

- [ ] **Step 3: Write minimal implementation**

```python
# algotrading/skills/indicators.py
def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    seed = sum(values[:period]) / period
    e = seed
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_indicators.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add algotrading/skills/indicators.py tests/test_indicators.py
git commit -m "feat: EMA and RSI indicators

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Strategy (skill) base interface

**Files:**
- Create: `algotrading/skills/base.py`
- Test: `tests/test_skill_base.py`

**Interfaces:**
- Produces: `Strategy` abstract base with attribute `name: str` and method `on_candle(self, candles: list[Candle], position: Position | None) -> Signal`. Consumes `models.Signal`, `models.Candle`, `models.Position`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skill_base.py
import pytest
from algotrading.skills.base import Strategy
from algotrading.models import Signal


class Dummy(Strategy):
    name = "dummy"

    def on_candle(self, candles, position):
        return Signal(action="HOLD", reason="dummy")


def test_cannot_instantiate_abstract():
    with pytest.raises(TypeError):
        Strategy()


def test_subclass_returns_signal():
    s = Dummy()
    sig = s.on_candle([], None)
    assert isinstance(sig, Signal)
    assert sig.action == "HOLD"
    assert s.name == "dummy"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_skill_base.py -v`
Expected: FAIL with `ImportError: cannot import name 'Strategy'`

- [ ] **Step 3: Write minimal implementation**

```python
# algotrading/skills/base.py
from abc import ABC, abstractmethod
from algotrading.models import Candle, Position, Signal


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def on_candle(self, candles: list[Candle], position: Position | None) -> Signal:
        """Return a trading Signal given the rolling candle window and current position."""
        raise NotImplementedError
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_skill_base.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add algotrading/skills/base.py tests/test_skill_base.py
git commit -m "feat: Strategy (skill) base interface

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: SQLite persistence layer

**Files:**
- Create: `algotrading/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces a `Database` class:
  - `Database(path: str)` — opens connection, creates schema if absent.
  - `ensure_strategy(name: str, starting_balance: float) -> None` — idempotent insert of strategy + wallet row.
  - `get_balance(name: str) -> float`
  - `set_balance(name: str, balance: float) -> None`
  - `open_position(pos: Position) -> int` — inserts, returns position id.
  - `close_position(position_id: int) -> None` — marks status closed.
  - `get_open_position(name: str) -> Position | None`
  - `record_trade(strategy: str, position_id: int | None, side: str, action: str, price: float, size: float, fee: float, pnl: float, ts: int) -> None`
  - `record_equity(strategy: str, equity: float, ts: int) -> None`
  - `log_decision(strategy: str, ts: int, candle_time: int, action: str, confidence: float, reason: str, indicators: dict) -> None`
  - `get_state(key: str) -> str | None` / `set_state(key: str, value: str) -> None`
  - `close() -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
from algotrading.db import Database
from algotrading.models import Position


def make_db(tmp_path):
    return Database(str(tmp_path / "test.db"))


def test_ensure_strategy_idempotent(tmp_path):
    db = make_db(tmp_path)
    db.ensure_strategy("ma_trend", 5000.0)
    db.ensure_strategy("ma_trend", 5000.0)  # second call must not duplicate/reset
    assert db.get_balance("ma_trend") == 5000.0


def test_set_and_get_balance(tmp_path):
    db = make_db(tmp_path)
    db.ensure_strategy("rsi_revert", 5000.0)
    db.set_balance("rsi_revert", 4800.0)
    assert db.get_balance("rsi_revert") == 4800.0


def test_open_get_close_position(tmp_path):
    db = make_db(tmp_path)
    db.ensure_strategy("ma_trend", 5000.0)
    pos = Position(strategy="ma_trend", side="LONG", entry_price=100.0, size=0.5,
                   leverage=3.0, margin=50.0, stop_price=98.0, opened_at=1000)
    pid = db.open_position(pos)
    assert isinstance(pid, int)
    fetched = db.get_open_position("ma_trend")
    assert fetched is not None and fetched.id == pid and fetched.side == "LONG"
    db.close_position(pid)
    assert db.get_open_position("ma_trend") is None


def test_state_roundtrip(tmp_path):
    db = make_db(tmp_path)
    assert db.get_state("last_candle_time") is None
    db.set_state("last_candle_time", "1717000000000")
    assert db.get_state("last_candle_time") == "1717000000000"


def test_records_do_not_raise(tmp_path):
    db = make_db(tmp_path)
    db.ensure_strategy("grid", 5000.0)
    db.record_trade("grid", None, "LONG", "OPEN", 100.0, 0.5, 0.05, 0.0, 1000)
    db.record_equity("grid", 5000.0, 1000)
    db.log_decision("grid", 1000, 999, "HOLD", 0.0, "no signal", {"rsi": 55})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_db.py -v`
Expected: FAIL with `ImportError: cannot import name 'Database'`

- [ ] **Step 3: Write minimal implementation**

```python
# algotrading/db.py
import json
import os
import sqlite3
from algotrading.models import Position

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

    def close(self) -> None:
        self.conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_db.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add algotrading/db.py tests/test_db.py
git commit -m "feat: SQLite persistence layer

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Broker — sizing, fills, fees, P&L (core math)

**Files:**
- Create: `algotrading/broker.py`
- Test: `tests/test_broker_core.py`

**Interfaces:**
- Produces a `Broker` class constructed as `Broker(fee: float, slippage: float)`:
  - `fill_price(price: float, side: str, is_entry: bool) -> float` — applies slippage adversely. Buying (LONG entry, SHORT exit) pays `price*(1+slippage)`; selling (SHORT entry, LONG exit) gets `price*(1-slippage)`.
  - `position_size(balance, entry_price, risk_pct, stop_pct, leverage) -> tuple[float, float]` — returns `(size, margin)`. `size = (balance*risk_pct)/(entry_price*stop_pct)`, capped so `margin = size*entry_price/leverage <= balance`.
  - `stop_price(entry_price, side, stop_pct) -> float` — LONG: `entry*(1-stop_pct)`; SHORT: `entry*(1+stop_pct)`.
  - `entry_fee(size, fill) -> float` — `size*fill*fee`.
  - `realized_pnl(position: Position, exit_fill: float) -> float` — LONG: `size*(exit_fill-entry)`; SHORT: `size*(entry-exit_fill)`. (Fees handled separately by caller.)
  - `unrealized_pnl(position: Position, price: float) -> float` — same formula at current mark price.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_broker_core.py
from algotrading.broker import Broker
from algotrading.models import Position

B = Broker(fee=0.0005, slippage=0.0005)


def test_fill_price_buy_is_higher():
    assert B.fill_price(100.0, "LONG", is_entry=True) == 100.0 * 1.0005


def test_fill_price_sell_is_lower():
    assert B.fill_price(100.0, "LONG", is_entry=False) == 100.0 * 0.9995


def test_fill_price_short_entry_is_sell():
    assert B.fill_price(100.0, "SHORT", is_entry=True) == 100.0 * 0.9995


def test_position_size_formula():
    # balance 5000, risk 2% -> risk 100; stop 2% of price; price 100
    # size = 100 / (100 * 0.02) = 50 ; margin = 50*100/3 = 1666.67 <= 5000
    size, margin = B.position_size(5000.0, 100.0, 0.02, 0.02, 3.0)
    assert round(size, 6) == 50.0
    assert round(margin, 2) == 1666.67


def test_position_size_capped_by_margin():
    # tiny stop -> huge size -> margin would exceed balance, so it gets capped
    size, margin = B.position_size(5000.0, 100.0, 0.5, 0.001, 3.0)
    assert round(margin, 2) <= 5000.0
    # capped: margin == balance, size = balance*leverage/price = 5000*3/100 = 150
    assert round(size, 6) == 150.0


def test_stop_price_long_and_short():
    assert B.stop_price(100.0, "LONG", 0.02) == 98.0
    assert B.stop_price(100.0, "SHORT", 0.02) == 102.0


def test_realized_pnl_long_profit():
    pos = Position(strategy="x", side="LONG", entry_price=100.0, size=2.0,
                   leverage=3.0, margin=66.7, stop_price=98.0, opened_at=0)
    assert B.realized_pnl(pos, 110.0) == 20.0


def test_realized_pnl_short_profit():
    pos = Position(strategy="x", side="SHORT", entry_price=100.0, size=2.0,
                   leverage=3.0, margin=66.7, stop_price=102.0, opened_at=0)
    assert B.realized_pnl(pos, 90.0) == 20.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_broker_core.py -v`
Expected: FAIL with `ImportError: cannot import name 'Broker'`

- [ ] **Step 3: Write minimal implementation**

```python
# algotrading/broker.py
from algotrading.models import Position


class Broker:
    def __init__(self, fee: float, slippage: float):
        self.fee = fee
        self.slippage = slippage

    def fill_price(self, price: float, side: str, is_entry: bool) -> float:
        # Determine whether this fill is a buy or a sell.
        buying = (side == "LONG" and is_entry) or (side == "SHORT" and not is_entry)
        if buying:
            return price * (1 + self.slippage)
        return price * (1 - self.slippage)

    def position_size(self, balance, entry_price, risk_pct, stop_pct, leverage) -> tuple[float, float]:
        size = (balance * risk_pct) / (entry_price * stop_pct)
        margin = size * entry_price / leverage
        if margin > balance:
            margin = balance
            size = balance * leverage / entry_price
        return size, margin

    def stop_price(self, entry_price: float, side: str, stop_pct: float) -> float:
        if side == "LONG":
            return entry_price * (1 - stop_pct)
        return entry_price * (1 + stop_pct)

    def entry_fee(self, size: float, fill: float) -> float:
        return size * fill * self.fee

    def realized_pnl(self, position: Position, exit_fill: float) -> float:
        if position.side == "LONG":
            return position.size * (exit_fill - position.entry_price)
        return position.size * (position.entry_price - exit_fill)

    def unrealized_pnl(self, position: Position, price: float) -> float:
        if position.side == "LONG":
            return position.size * (price - position.entry_price)
        return position.size * (position.entry_price - price)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_broker_core.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add algotrading/broker.py tests/test_broker_core.py
git commit -m "feat: broker core math — sizing, slippage fills, fees, P&L

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Broker — stop-loss & liquidation checks

**Files:**
- Modify: `algotrading/broker.py`
- Test: `tests/test_broker_risk.py`

**Interfaces:**
- Consumes: `Broker` from Task 5.
- Produces (added to `Broker`):
  - `liquidation_price(position: Position) -> float` — LONG: `entry*(1 - 1/leverage)`; SHORT: `entry*(1 + 1/leverage)`.
  - `hit_stop(position: Position, candle: Candle) -> bool` — LONG: `candle.low <= stop_price`; SHORT: `candle.high >= stop_price`.
  - `hit_liquidation(position: Position, candle: Candle) -> bool` — LONG: `candle.low <= liquidation_price`; SHORT: `candle.high >= liquidation_price`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_broker_risk.py
from algotrading.broker import Broker
from algotrading.models import Position, Candle

B = Broker(fee=0.0005, slippage=0.0005)


def long_pos():
    return Position(strategy="x", side="LONG", entry_price=100.0, size=1.0,
                    leverage=3.0, margin=33.3, stop_price=98.0, opened_at=0)


def short_pos():
    return Position(strategy="x", side="SHORT", entry_price=100.0, size=1.0,
                    leverage=3.0, margin=33.3, stop_price=102.0, opened_at=0)


def candle(high, low):
    return Candle(open=100, high=high, low=low, close=100, volume=1, time=0)


def test_liquidation_price_long():
    assert round(B.liquidation_price(long_pos()), 4) == round(100 * (1 - 1/3), 4)


def test_liquidation_price_short():
    assert round(B.liquidation_price(short_pos()), 4) == round(100 * (1 + 1/3), 4)


def test_hit_stop_long_true_when_low_breaches():
    assert B.hit_stop(long_pos(), candle(high=101, low=97.5)) is True


def test_hit_stop_long_false_when_above():
    assert B.hit_stop(long_pos(), candle(high=101, low=99)) is False


def test_hit_stop_short_true_when_high_breaches():
    assert B.hit_stop(short_pos(), candle(high=103, low=99)) is True


def test_hit_liquidation_long():
    assert B.hit_liquidation(long_pos(), candle(high=100, low=66.0)) is True
    assert B.hit_liquidation(long_pos(), candle(high=100, low=70.0)) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_broker_risk.py -v`
Expected: FAIL with `AttributeError: 'Broker' object has no attribute 'liquidation_price'`

- [ ] **Step 3: Add implementation to broker.py**

Add `Candle` to the import and append these methods to the `Broker` class:

```python
# at top of algotrading/broker.py, update the import line:
from algotrading.models import Candle, Position
```

```python
    # append inside class Broker:
    def liquidation_price(self, position: Position) -> float:
        if position.side == "LONG":
            return position.entry_price * (1 - 1 / position.leverage)
        return position.entry_price * (1 + 1 / position.leverage)

    def hit_stop(self, position: Position, candle: Candle) -> bool:
        if position.side == "LONG":
            return candle.low <= position.stop_price
        return candle.high >= position.stop_price

    def hit_liquidation(self, position: Position, candle: Candle) -> bool:
        liq = self.liquidation_price(position)
        if position.side == "LONG":
            return candle.low <= liq
        return candle.high >= liq
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_broker_risk.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add algotrading/broker.py tests/test_broker_risk.py
git commit -m "feat: broker stop-loss and liquidation checks

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Skill — MA crossover trend

**Files:**
- Create: `algotrading/skills/ma_trend.py`
- Test: `tests/test_ma_trend.py`

**Interfaces:**
- Consumes: `Strategy` (Task 3), `ema` (Task 2), `Candle`/`Signal`/`Position`.
- Produces: `MaTrend(fast: int = 9, slow: int = 21)` with `name = "ma_trend"`. Emits `LONG` on fast-over-slow crossover up, `SHORT` on crossover down, else `HOLD`. Returns `HOLD` (with reason "warming up") when not enough candles.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ma_trend.py
from algotrading.skills.ma_trend import MaTrend
from algotrading.models import Candle


def candles_from(closes):
    return [Candle(open=c, high=c, low=c, close=c, volume=1, time=i)
            for i, c in enumerate(closes)]


def test_warming_up_returns_hold():
    s = MaTrend(fast=3, slow=5)
    sig = s.on_candle(candles_from([1, 2, 3]), None)
    assert sig.action == "HOLD"


def test_crossover_up_returns_long():
    s = MaTrend(fast=3, slow=5)
    # downtrend then sharp up so fast EMA crosses above slow on the last candle
    closes = [10, 9, 8, 7, 6, 5, 4, 20, 30]
    sig = s.on_candle(candles_from(closes), None)
    assert sig.action == "LONG"
    assert "fast" in sig.indicators


def test_crossover_down_returns_short():
    s = MaTrend(fast=3, slow=5)
    closes = [1, 2, 3, 4, 5, 6, 7, 1, 0.5]
    sig = s.on_candle(candles_from(closes), None)
    assert sig.action == "SHORT"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ma_trend.py -v`
Expected: FAIL with `ImportError: cannot import name 'MaTrend'`

- [ ] **Step 3: Write minimal implementation**

```python
# algotrading/skills/ma_trend.py
from algotrading.skills.base import Strategy
from algotrading.skills.indicators import ema
from algotrading.models import Candle, Position, Signal


class MaTrend(Strategy):
    name = "ma_trend"

    def __init__(self, fast: int = 9, slow: int = 21):
        self.fast = fast
        self.slow = slow

    def on_candle(self, candles: list[Candle], position: Position | None) -> Signal:
        closes = [c.close for c in candles]
        if len(closes) < self.slow + 1:
            return Signal(action="HOLD", reason="warming up")
        fast_now = ema(closes, self.fast)
        slow_now = ema(closes, self.slow)
        fast_prev = ema(closes[:-1], self.fast)
        slow_prev = ema(closes[:-1], self.slow)
        ind = {"fast": round(fast_now, 4), "slow": round(slow_now, 4)}
        if fast_prev <= slow_prev and fast_now > slow_now:
            return Signal(action="LONG", confidence=0.6,
                          reason=f"EMA{self.fast} crossed above EMA{self.slow}", indicators=ind)
        if fast_prev >= slow_prev and fast_now < slow_now:
            return Signal(action="SHORT", confidence=0.6,
                          reason=f"EMA{self.fast} crossed below EMA{self.slow}", indicators=ind)
        return Signal(action="HOLD", reason="no crossover", indicators=ind)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ma_trend.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add algotrading/skills/ma_trend.py tests/test_ma_trend.py
git commit -m "feat: MA crossover trend skill

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Skill — RSI mean-reversion

**Files:**
- Create: `algotrading/skills/rsi_revert.py`
- Test: `tests/test_rsi_revert.py`

**Interfaces:**
- Consumes: `Strategy`, `rsi`, `Candle`/`Signal`/`Position`.
- Produces: `RsiRevert(period: int = 14, oversold: float = 30, overbought: float = 70)` with `name = "rsi_revert"`. RSI < oversold and no position → `LONG`. RSI > overbought and holding a LONG → `CLOSE`. Otherwise `HOLD`. `HOLD` ("warming up") if not enough data.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rsi_revert.py
from algotrading.skills.rsi_revert import RsiRevert
from algotrading.models import Candle, Position


def candles_from(closes):
    return [Candle(open=c, high=c, low=c, close=c, volume=1, time=i)
            for i, c in enumerate(closes)]


def test_warming_up_returns_hold():
    s = RsiRevert(period=14)
    sig = s.on_candle(candles_from([1, 2, 3]), None)
    assert sig.action == "HOLD"


def test_oversold_opens_long():
    s = RsiRevert(period=14)
    closes = [float(x) for x in range(40, 9, -1)]  # strictly falling -> RSI very low
    sig = s.on_candle(candles_from(closes), None)
    assert sig.action == "LONG"


def test_overbought_closes_long():
    s = RsiRevert(period=14)
    closes = [float(x) for x in range(1, 32)]  # strictly rising -> RSI very high
    pos = Position(strategy="rsi_revert", side="LONG", entry_price=10, size=1,
                   leverage=3, margin=3, stop_price=9, opened_at=0)
    sig = s.on_candle(candles_from(closes), pos)
    assert sig.action == "CLOSE"


def test_rising_no_position_holds():
    s = RsiRevert(period=14)
    closes = [float(x) for x in range(1, 32)]
    sig = s.on_candle(candles_from(closes), None)
    assert sig.action == "HOLD"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_rsi_revert.py -v`
Expected: FAIL with `ImportError: cannot import name 'RsiRevert'`

- [ ] **Step 3: Write minimal implementation**

```python
# algotrading/skills/rsi_revert.py
from algotrading.skills.base import Strategy
from algotrading.skills.indicators import rsi
from algotrading.models import Candle, Position, Signal


class RsiRevert(Strategy):
    name = "rsi_revert"

    def __init__(self, period: int = 14, oversold: float = 30, overbought: float = 70):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def on_candle(self, candles: list[Candle], position: Position | None) -> Signal:
        closes = [c.close for c in candles]
        value = rsi(closes, self.period)
        if value is None:
            return Signal(action="HOLD", reason="warming up")
        ind = {"rsi": round(value, 2)}
        if position is None and value < self.oversold:
            return Signal(action="LONG", confidence=0.6,
                          reason=f"RSI {value:.1f} < {self.oversold} (oversold)", indicators=ind)
        if position is not None and position.side == "LONG" and value > self.overbought:
            return Signal(action="CLOSE", confidence=0.6,
                          reason=f"RSI {value:.1f} > {self.overbought} (overbought)", indicators=ind)
        return Signal(action="HOLD", reason="no extreme", indicators=ind)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_rsi_revert.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add algotrading/skills/rsi_revert.py tests/test_rsi_revert.py
git commit -m "feat: RSI mean-reversion skill

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Skill — Grid (simplified band)

**Files:**
- Create: `algotrading/skills/grid.py`
- Test: `tests/test_grid.py`

**Interfaces:**
- Consumes: `Strategy`, `Candle`/`Signal`/`Position`.
- Produces: `Grid(levels: int = 5, band_pct: float = 0.02, lookback: int = 60)` with `name = "grid"`. Reference price = mean close over the lookback window. Band = `[ref*(1-band_pct), ref*(1+band_pct)]`. If flat and price near/below the lowest grid level (`<= ref*(1-band_pct)`) → `LONG`. If holding a LONG and price near/above the top level (`>= ref*(1+band_pct)`) → `CLOSE`. Else `HOLD`. `HOLD` ("warming up") if fewer than `lookback` candles.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grid.py
from algotrading.skills.grid import Grid
from algotrading.models import Candle, Position


def candles_from(closes):
    return [Candle(open=c, high=c, low=c, close=c, volume=1, time=i)
            for i, c in enumerate(closes)]


def test_warming_up_returns_hold():
    s = Grid(levels=5, band_pct=0.02, lookback=60)
    sig = s.on_candle(candles_from([100] * 10), None)
    assert sig.action == "HOLD"


def test_buy_at_bottom_of_band():
    s = Grid(levels=5, band_pct=0.02, lookback=10)
    closes = [100.0] * 9 + [97.0]  # last price below ref*(1-0.02)=98
    sig = s.on_candle(candles_from(closes), None)
    assert sig.action == "LONG"


def test_close_at_top_of_band():
    s = Grid(levels=5, band_pct=0.02, lookback=10)
    closes = [100.0] * 9 + [103.0]  # last price above ref*(1+0.02)=102
    pos = Position(strategy="grid", side="LONG", entry_price=98, size=1,
                   leverage=3, margin=33, stop_price=96, opened_at=0)
    sig = s.on_candle(candles_from(closes), pos)
    assert sig.action == "CLOSE"


def test_middle_holds():
    s = Grid(levels=5, band_pct=0.02, lookback=10)
    closes = [100.0] * 10
    sig = s.on_candle(candles_from(closes), None)
    assert sig.action == "HOLD"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_grid.py -v`
Expected: FAIL with `ImportError: cannot import name 'Grid'`

- [ ] **Step 3: Write minimal implementation**

```python
# algotrading/skills/grid.py
from algotrading.skills.base import Strategy
from algotrading.models import Candle, Position, Signal


class Grid(Strategy):
    name = "grid"

    def __init__(self, levels: int = 5, band_pct: float = 0.02, lookback: int = 60):
        self.levels = levels
        self.band_pct = band_pct
        self.lookback = lookback

    def on_candle(self, candles: list[Candle], position: Position | None) -> Signal:
        if len(candles) < self.lookback:
            return Signal(action="HOLD", reason="warming up")
        window = candles[-self.lookback:]
        ref = sum(c.close for c in window) / len(window)
        lower = ref * (1 - self.band_pct)
        upper = ref * (1 + self.band_pct)
        price = candles[-1].close
        ind = {"ref": round(ref, 2), "lower": round(lower, 2), "upper": round(upper, 2),
               "price": round(price, 2)}
        if position is None and price <= lower:
            return Signal(action="LONG", confidence=0.5,
                          reason=f"price {price:.2f} at/below grid bottom {lower:.2f}", indicators=ind)
        if position is not None and position.side == "LONG" and price >= upper:
            return Signal(action="CLOSE", confidence=0.5,
                          reason=f"price {price:.2f} at/above grid top {upper:.2f}", indicators=ind)
        return Signal(action="HOLD", reason="inside band", indicators=ind)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_grid.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add algotrading/skills/grid.py tests/test_grid.py
git commit -m "feat: grid trading skill (simplified band)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Data feed — CoinDCX candles parsing + fetch

**Files:**
- Create: `algotrading/feed.py`
- Create: `tests/fixtures/candles_sample.json`
- Test: `tests/test_feed.py`

**Interfaces:**
- Produces:
  - `parse_candles(raw: list[dict]) -> list[Candle]` — maps CoinDCX candle dicts (`open/high/low/close/volume/time`) to `Candle`, sorted ascending by `time`.
  - `get_candles(pair: str, interval: str, limit: int = 200, *, fetcher=None) -> list[Candle]` — calls the candles endpoint via `fetcher` (defaults to a `requests`-based fetch) and returns parsed candles. `fetcher(url, params) -> list[dict]` is injectable for testing (no live network in tests).
  - Module constant `CANDLES_URL = "https://public.coindcx.com/market_data/candles"`.

- [ ] **Step 1: Create the fixture and write the failing test**

```json
// tests/fixtures/candles_sample.json
[
  {"open": 100.0, "high": 105.0, "low": 99.0, "close": 104.0, "volume": 12.5, "time": 1717000120000},
  {"open": 104.0, "high": 106.0, "low": 103.0, "close": 105.0, "volume": 8.0, "time": 1717000060000},
  {"open": 98.0, "high": 101.0, "low": 97.0, "close": 100.0, "volume": 10.0, "time": 1717000000000}
]
```

```python
# tests/test_feed.py
import json
from pathlib import Path
from algotrading.feed import parse_candles, get_candles, CANDLES_URL

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "candles_sample.json").read_text())


def test_parse_sorts_ascending_by_time():
    candles = parse_candles(FIXTURE)
    times = [c.time for c in candles]
    assert times == sorted(times)
    assert candles[0].time == 1717000000000
    assert candles[-1].close == 104.0


def test_get_candles_uses_injected_fetcher():
    captured = {}

    def fake_fetcher(url, params):
        captured["url"] = url
        captured["params"] = params
        return FIXTURE

    candles = get_candles("I-BTC_INR", "1m", limit=3, fetcher=fake_fetcher)
    assert captured["url"] == CANDLES_URL
    assert captured["params"]["pair"] == "I-BTC_INR"
    assert captured["params"]["interval"] == "1m"
    assert captured["params"]["limit"] == 3
    assert len(candles) == 3
    assert candles[0].time < candles[-1].time
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_feed.py -v`
Expected: FAIL with `ImportError: cannot import name 'parse_candles'`

- [ ] **Step 3: Write minimal implementation**

```python
# algotrading/feed.py
import requests
from algotrading.models import Candle

CANDLES_URL = "https://public.coindcx.com/market_data/candles"


def parse_candles(raw: list[dict]) -> list[Candle]:
    candles = [
        Candle(open=float(r["open"]), high=float(r["high"]), low=float(r["low"]),
               close=float(r["close"]), volume=float(r["volume"]), time=int(r["time"]))
        for r in raw
    ]
    candles.sort(key=lambda c: c.time)
    return candles


def _http_fetcher(url: str, params: dict) -> list[dict]:
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_candles(pair: str, interval: str, limit: int = 200, *, fetcher=None) -> list[Candle]:
    fetcher = fetcher or _http_fetcher
    params = {"pair": pair, "interval": interval, "limit": limit}
    raw = fetcher(CANDLES_URL, params)
    return parse_candles(raw)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_feed.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add algotrading/feed.py tests/test_feed.py tests/fixtures/candles_sample.json
git commit -m "feat: CoinDCX candles feed (parse + injectable fetch)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Engine — wire feed → skills → broker → db (one tick)

**Files:**
- Create: `algotrading/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `CONFIG`, `Database`, `Broker`, `get_candles`, all three skills, `Signal`/`Position`.
- Produces:
  - `build_skills(names: list[str]) -> list[Strategy]` — maps config skill names to instances (`ma_trend`→`MaTrend()`, `rsi_revert`→`RsiRevert()`, `grid`→`Grid()`).
  - `process_tick(db, broker, skills, candles, cfg) -> None` — for each skill: check stop/liquidation on the open position (close if hit), then get a signal, log the decision, act (open on LONG/SHORT when flat, close on CLOSE when holding), update balance + record trade + record equity. Acts on the **last closed candle** only; caller guarantees a new candle.
  - `run(cfg=CONFIG, *, fetcher=None, max_ticks=None, sleeper=None) -> None` — the loop: ensure strategies, fetch candles, skip if `state['last_candle_time']` unchanged, else `process_tick`, persist cursor, sleep. `max_ticks`/`fetcher`/`sleeper` are injectable for tests.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_engine.py
import json
from pathlib import Path
from algotrading.engine import build_skills, process_tick, run
from algotrading.db import Database
from algotrading.broker import Broker
from algotrading.config import Config
from algotrading.models import Candle


def rising_then_drop():
    # enough candles to warm up skills (>61 for grid lookback default 60)
    closes = [100 + i for i in range(70)] + [50.0]
    return [Candle(open=c, high=c + 1, low=c - 1, close=c, volume=1, time=1000 + i * 60000)
            for i, c in enumerate(closes)]


def test_build_skills_returns_three():
    skills = build_skills(["ma_trend", "rsi_revert", "grid"])
    names = sorted(s.name for s in skills)
    assert names == ["grid", "ma_trend", "rsi_revert"]


def test_process_tick_persists_decisions(tmp_path):
    cfg = Config(db_path=str(tmp_path / "e.db"))
    db = Database(cfg.db_path)
    broker = Broker(cfg.fee, cfg.slippage)
    skills = build_skills(cfg.enabled_skills)
    for s in skills:
        db.ensure_strategy(s.name, cfg.starting_balance)
    candles = rising_then_drop()
    process_tick(db, broker, skills, candles, cfg)
    # a decision row must exist for every skill
    rows = db.conn.execute("SELECT DISTINCT strategy FROM decision_log").fetchall()
    assert len(rows) == 3
    db.close()


def test_run_processes_one_tick_then_stops(tmp_path):
    cfg = Config(db_path=str(tmp_path / "r.db"))
    candles_raw = [{"open": c.open, "high": c.high, "low": c.low, "close": c.close,
                    "volume": c.volume, "time": c.time} for c in rising_then_drop()]

    def fake_fetcher(url, params):
        return candles_raw

    run(cfg, fetcher=fake_fetcher, max_ticks=1, sleeper=lambda s: None)
    db = Database(cfg.db_path)
    assert db.get_state("last_candle_time") == str(candles_raw[-1]["time"])
    db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_engine.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_skills'`

- [ ] **Step 3: Write minimal implementation**

```python
# algotrading/engine.py
import time
from algotrading.config import CONFIG
from algotrading.db import Database
from algotrading.broker import Broker
from algotrading.feed import get_candles
from algotrading.models import Position
from algotrading.skills.ma_trend import MaTrend
from algotrading.skills.rsi_revert import RsiRevert
from algotrading.skills.grid import Grid

_SKILL_FACTORY = {
    "ma_trend": MaTrend,
    "rsi_revert": RsiRevert,
    "grid": Grid,
}


def build_skills(names):
    return [_SKILL_FACTORY[n]() for n in names if n in _SKILL_FACTORY]


def _open(db, broker, skill, candle, cfg):
    balance = db.get_balance(skill.name)
    return balance


def _close_position(db, broker, skill, position, exit_price, candle, reason):
    exit_fill = broker.fill_price(exit_price, position.side, is_entry=False)
    pnl = broker.realized_pnl(position, exit_fill)
    fee = broker.entry_fee(position.size, exit_fill)
    balance = db.get_balance(skill.name) + pnl - fee
    db.set_balance(skill.name, balance)
    db.close_position(position.id)
    db.record_trade(skill.name, position.id, position.side, "CLOSE", exit_fill,
                    position.size, fee, pnl - fee, candle.time)
    return balance


def _open_position(db, broker, skill, side, candle, cfg):
    entry_fill = broker.fill_price(candle.close, side, is_entry=True)
    size, margin = broker.position_size(
        db.get_balance(skill.name), entry_fill, cfg.risk_pct, cfg.stop_pct, cfg.leverage)
    stop = broker.stop_price(entry_fill, side, cfg.stop_pct)
    fee = broker.entry_fee(size, entry_fill)
    balance = db.get_balance(skill.name) - fee
    db.set_balance(skill.name, balance)
    pos = Position(strategy=skill.name, side=side, entry_price=entry_fill, size=size,
                   leverage=cfg.leverage, margin=margin, stop_price=stop, opened_at=candle.time)
    pid = db.open_position(pos)
    db.record_trade(skill.name, pid, side, "OPEN", entry_fill, size, fee, -fee, candle.time)


def process_tick(db, broker, skills, candles, cfg):
    candle = candles[-1]
    for skill in skills:
        position = db.get_open_position(skill.name)
        # 1. risk checks on existing position
        if position is not None:
            if broker.hit_liquidation(position, candle):
                _close_position(db, broker, skill, position, broker.liquidation_price(position),
                                candle, "liquidation")
                position = None
            elif broker.hit_stop(position, candle):
                _close_position(db, broker, skill, position, position.stop_price,
                                candle, "stop")
                position = None
        # 2. strategy decision
        signal = skill.on_candle(candles, position)
        db.log_decision(skill.name, candle.time, candle.time, signal.action,
                        signal.confidence, signal.reason, signal.indicators)
        # 3. act
        if signal.action in ("LONG", "SHORT") and position is None:
            _open_position(db, broker, skill, signal.action, candle, cfg)
        elif signal.action == "CLOSE" and position is not None:
            _close_position(db, broker, skill, position, candle.close, candle, "signal")
        # 4. equity snapshot
        pos_now = db.get_open_position(skill.name)
        unreal = broker.unrealized_pnl(pos_now, candle.close) if pos_now else 0.0
        db.record_equity(skill.name, db.get_balance(skill.name) + unreal, candle.time)


def run(cfg=CONFIG, *, fetcher=None, max_ticks=None, sleeper=None):
    sleeper = sleeper or time.sleep
    db = Database(cfg.db_path)
    broker = Broker(cfg.fee, cfg.slippage)
    skills = build_skills(cfg.enabled_skills)
    for s in skills:
        db.ensure_strategy(s.name, cfg.starting_balance)
    ticks = 0
    try:
        while max_ticks is None or ticks < max_ticks:
            candles = get_candles(cfg.pair_candles, cfg.interval, fetcher=fetcher)
            if candles:
                newest = str(candles[-1].time)
                if db.get_state("last_candle_time") != newest:
                    process_tick(db, broker, skills, candles, cfg)
                    db.set_state("last_candle_time", newest)
            ticks += 1
            if max_ticks is None or ticks < max_ticks:
                sleeper(cfg.poll_seconds)
    finally:
        db.close()


if __name__ == "__main__":
    run()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_engine.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the whole suite, then commit**

Run: `python -m pytest -v`
Expected: ALL PASS

```bash
git add algotrading/engine.py tests/test_engine.py
git commit -m "feat: engine loop wiring feed, skills, broker, db

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Report — leaderboard + metrics CLI

**Files:**
- Create: `algotrading/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `Database`.
- Produces:
  - `compute_stats(db, strategy, starting_balance) -> dict` — returns `{equity, return_pct, realized_pnl, trades, wins, losses, win_rate, max_drawdown}` computed from `trades` (closed rows with non-zero realized pnl) and `equity` tables. `equity` = latest equity row (fallback to balance).
  - `leaderboard(db, strategies, starting_balance) -> list[dict]` — stats per strategy sorted by `equity` desc.
  - `format_leaderboard(rows) -> str` — a plain-text table.
  - `main(cfg=CONFIG) -> None` — prints the leaderboard.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report.py
from algotrading.db import Database
from algotrading.report import compute_stats, leaderboard, format_leaderboard


def seed(tmp_path):
    db = Database(str(tmp_path / "rep.db"))
    db.ensure_strategy("ma_trend", 5000.0)
    # two closed trades: one win (+200), one loss (-100)
    db.record_trade("ma_trend", 1, "LONG", "CLOSE", 110.0, 2.0, 0.1, 200.0, 1000)
    db.record_trade("ma_trend", 2, "LONG", "CLOSE", 90.0, 2.0, 0.1, -100.0, 2000)
    db.record_equity("ma_trend", 5100.0, 2000)
    return db


def test_compute_stats_counts_wins_losses(tmp_path):
    db = seed(tmp_path)
    stats = compute_stats(db, "ma_trend", 5000.0)
    assert stats["trades"] == 2
    assert stats["wins"] == 1
    assert stats["losses"] == 1
    assert stats["win_rate"] == 0.5
    assert stats["equity"] == 5100.0
    assert round(stats["return_pct"], 2) == 2.0


def test_leaderboard_sorted_by_equity(tmp_path):
    db = seed(tmp_path)
    db.ensure_strategy("grid", 5000.0)
    db.record_equity("grid", 5200.0, 2000)
    rows = leaderboard(db, ["ma_trend", "grid"], 5000.0)
    assert rows[0]["strategy"] == "grid"  # higher equity first


def test_format_leaderboard_is_string(tmp_path):
    db = seed(tmp_path)
    rows = leaderboard(db, ["ma_trend"], 5000.0)
    out = format_leaderboard(rows)
    assert "ma_trend" in out and "equity" in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_report.py -v`
Expected: FAIL with `ImportError: cannot import name 'compute_stats'`

- [ ] **Step 3: Write minimal implementation**

```python
# algotrading/report.py
from algotrading.config import CONFIG
from algotrading.db import Database


def compute_stats(db: Database, strategy: str, starting_balance: float) -> dict:
    closed = db.conn.execute(
        "SELECT pnl FROM trades WHERE strategy=? AND action='CLOSE'", (strategy,)
    ).fetchall()
    pnls = [float(r["pnl"]) for r in closed]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    trades = len(pnls)
    realized = sum(pnls)
    win_rate = (wins / trades) if trades else 0.0

    eq_rows = db.conn.execute(
        "SELECT equity FROM equity WHERE strategy=? ORDER BY ts ASC", (strategy,)
    ).fetchall()
    curve = [float(r["equity"]) for r in eq_rows]
    equity = curve[-1] if curve else db.get_balance(strategy)

    peak = starting_balance
    max_dd = 0.0
    for e in curve:
        peak = max(peak, e)
        dd = (peak - e) / peak if peak else 0.0
        max_dd = max(max_dd, dd)

    return {
        "strategy": strategy,
        "equity": equity,
        "return_pct": (equity - starting_balance) / starting_balance * 100,
        "realized_pnl": realized,
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "max_drawdown": max_dd,
    }


def leaderboard(db: Database, strategies: list[str], starting_balance: float) -> list[dict]:
    rows = [compute_stats(db, s, starting_balance) for s in strategies]
    rows.sort(key=lambda r: r["equity"], reverse=True)
    return rows


def format_leaderboard(rows: list[dict]) -> str:
    header = f"{'strategy':<12} {'equity':>10} {'return%':>9} {'trades':>7} {'win%':>6} {'maxDD%':>7}"
    lines = [header, "-" * len(header)]
    for r in rows:
        lines.append(
            f"{r['strategy']:<12} {r['equity']:>10.2f} {r['return_pct']:>8.2f}% "
            f"{r['trades']:>7} {r['win_rate']*100:>5.1f}% {r['max_drawdown']*100:>6.2f}%"
        )
    return "\n".join(lines)


def main(cfg=CONFIG) -> None:
    db = Database(cfg.db_path)
    try:
        rows = leaderboard(db, cfg.enabled_skills, cfg.starting_balance)
        print(format_leaderboard(rows))
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_report.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run full suite + smoke test, then commit**

Run: `python -m pytest -v`
Expected: ALL PASS

Smoke test against the real (live) feed for one tick, then show the leaderboard:

```bash
python -c "from algotrading.engine import run; run(max_ticks=1, sleeper=lambda s: None)"
python -m algotrading.report
```
Expected: no error; a leaderboard table prints with the 3 strategies.

```bash
git add algotrading/report.py tests/test_report.py
git commit -m "feat: leaderboard + metrics report CLI

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Data feed (spec §8) → Task 10. ✅
- Strategy interface (spec §5) → Task 3. ✅
- Seed strategies MA/RSI/Grid (spec §6) → Tasks 7, 8, 9. ✅
- Broker model: sizing, fills, fees, leverage, stop, liquidation (spec §7) → Tasks 5, 6. ✅
- SQLite schema incl. decision_log + state (spec §9) → Task 4. ✅
- Engine loop, restart-safe via state (spec §10) → Task 11. ✅
- Reporting/leaderboard + metrics (spec §11) → Task 12. ✅
- Config single source of truth (spec §12) → Task 1. ✅
- Testing approach (spec §15): unit per skill, broker math, db round-trip, feed parse from fixture → covered across tasks. ✅
- Decision logging / traceable chains (spec §1) → `decision_log` written every tick in Task 11. ✅

**Deferred to later phases (correctly out of Phase-1 scope):** backtester & Sharpe (Phase 2), ML/RL + multi-agent (Phase 3), live trading/daemon/alerts (Phase 4). Sharpe intentionally omitted from Task 12 per spec §11 ("come with the Phase-2 lab").

**Placeholder scan:** No TBD/TODO; every code step contains full code. ✅

**Type consistency:** `Candle`, `Signal`, `Position` fields consistent across all tasks. `Broker(fee, slippage)` constructor used identically in Tasks 5, 6, 11. `Database` method names match between Tasks 4, 11, 12. Skill constructors (`MaTrend()`, `RsiRevert()`, `Grid()`) match the factory in Task 11. ✅
