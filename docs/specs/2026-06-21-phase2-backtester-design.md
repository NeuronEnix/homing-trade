# Phase 2 — Backtester + Candle Storage — Design Spec

- **Date:** 2026-06-21
- **Status:** Approved (design); implementation pending
- **Depends on:** Phase 1 (engine, broker, skills, db, feed) — merged to `main`
- **Owner:** devansh@jum.bz

## 1. Goal

Let any strategy be evaluated against **historical** CoinDCX BTC/INR data in seconds
instead of waiting for live ticks, and stop re-fetching the same data from CoinDCX by
**persisting all candles locally in SQLite**. Scope is **backtest + report only** — no
parameter optimisation in this phase.

Two capabilities:
1. **Persistent candle store** — every candle we fetch (historical backfill *and* live
   polling) is saved to SQLite, so over time we accumulate our own dataset and reruns
   hit the local DB, not the exchange.
2. **Backtester** — replay stored candles through a strategy using the *same* execution
   path as the live engine, and report performance metrics.

## 2. Key Principle — reuse the live execution path

The backtester MUST NOT reimplement trade logic. It feeds candles through the existing
`engine.process_tick` + `broker.Broker`, swapping the SQLite `Database` for an in-memory
ledger with the same method surface. This guarantees a backtest is a faithful preview of
live behaviour (DRY; consistency), and the 57 existing Phase-1 tests keep that path correct.

The paper-first honesty rule still holds: a good backtest must be confirmed by live paper
trading before any real money. The report prints a one-line reminder to that effect.

## 3. Candle storage

### 3.1 Decision: one table with a `source` column (not two tables)

A candle at a given `(pair, interval, time)` is the same candle regardless of how it was
obtained. A single table keyed on `(pair, interval, time)` auto-dedupes and makes merged
backtesting a plain range query. A `source` column (`'history' | 'live'`) preserves
provenance. Two physical tables were rejected: they overlap in time (live data becomes
historical) and would require UNION+dedupe on every read, risking double-counted candles.

### 3.2 Schema (added to `db.py`)

```sql
CREATE TABLE IF NOT EXISTS candles (
    pair     TEXT    NOT NULL,
    interval TEXT    NOT NULL,
    time     INTEGER NOT NULL,   -- candle open time, epoch ms
    open     REAL    NOT NULL,
    high     REAL    NOT NULL,
    low      REAL    NOT NULL,
    close    REAL    NOT NULL,
    volume   REAL    NOT NULL,
    source   TEXT    NOT NULL,   -- 'history' | 'live'
    PRIMARY KEY (pair, interval, time)
);
```

### 3.3 New `Database` methods

- `save_candles(pair: str, interval: str, candles: list[Candle], source: str) -> int`
  Upserts each candle (`INSERT ... ON CONFLICT(pair,interval,time) DO UPDATE` so a
  later, more-complete fetch corrects an earlier partial one). Returns count written.
- `get_candles_range(pair, interval, start_ms, end_ms, source: str = "all") -> list[Candle]`
  Returns candles with `start_ms <= time <= end_ms`, sorted ascending by time. When
  `source` is "live" or "history" it filters on the column; "all" returns both (merged).
- `get_candle_bounds(pair, interval) -> tuple[int, int] | None`
  Returns `(min_time, max_time)` of stored candles for the pair/interval, or `None` if none.
- `count_candles(pair, interval, source: str = "all") -> int`

The DB file remains `data/paper_trading.db`. Candles live alongside the existing
strategy/wallet/trade tables; backtests use an isolated in-memory ledger and never write to
the strategy/wallet/trade/equity tables.

## 4. Capture live candles

`engine.run()` already fetches a candle window every tick. After a successful fetch it calls
`db.save_candles(cfg.pair_candles, cfg.interval, candles, source="live")`. This is the only
change to Phase-1 engine behaviour and is additive — the trading logic is untouched and the
existing engine tests still pass (the test fetcher returns candles, which now also get
persisted to the test's tmp DB; assertions are unaffected).

## 5. History backfill — `history.py`

- `CANDLE_INTERVAL_MS: dict[str, int]` — milliseconds per supported interval (e.g. `"1m"`:
  60_000, `"5m"`: 300_000, `"1h"`: 3_600_000, `"1d"`: 86_400_000). Used to compute ranges
  and page sizes.
- `ensure_history(db, pair, interval, days, now_ms, *, fetcher=None) -> list[Candle]`
  1. Compute desired range `[now_ms - days*86_400_000, now_ms]`.
  2. Read `db.get_candle_bounds(pair, interval)` to see what is already stored.
  3. Fetch only the missing span(s) from CoinDCX, paging in chunks of ≤1000 candles using
     the candles endpoint's `startTime`/`endTime`/`limit` params (reuses `feed`'s endpoint
     and an injectable `fetcher`). Save each page with `db.save_candles(..., source="history")`.
  4. Return `db.get_candles_range(pair, interval, start, end, source="all")`.
  `now_ms` is passed in by the caller (not read inside, so tests are deterministic).
- On a fetch error mid-paging: stop paging, keep what was saved, and return whatever the DB
  has for the range (log a warning). Never crash the caller.

## 6. In-memory ledger — `ledger.py`

`MemoryLedger` implements exactly the method surface `engine.process_tick` and its helpers
call on `Database`, but in memory:

- `__init__(self, strategy: str, starting_balance: float)`
- `ensure_strategy(name, starting_balance)` — no-op/idempotent (interface parity)
- `get_balance(name) -> float` / `set_balance(name, balance)`
- `open_position(pos: Position) -> int` (assigns an incrementing id, stores it)
- `close_position(position_id)` / `get_open_position(name) -> Position | None`
- `record_trade(strategy, position_id, side, action, price, size, fee, pnl, ts)` — appends to `self.trades`
- `record_equity(strategy, equity, ts)` — appends `(ts, equity)` to `self.equity_curve`
- `log_decision(...)` — no-op (decisions aren't needed for metrics; kept for interface parity)

It holds: `self.trades: list[dict]`, `self.equity_curve: list[tuple[int, float]]`. No SQLite,
no files. One ledger per strategy per backtest run.

## 7. Metrics — `metrics.py`

Pure functions over a closed-trade list and an equity curve:

- `total_return_pct(start_balance, final_equity) -> float`
- `win_rate(trades) -> float` — fraction of CLOSE trades with pnl > 0 (0.0 if none)
- `profit_factor(trades) -> float` — gross profit / gross loss over CLOSE trades; `inf` if no
  losses and some profit; 0.0 if no trades.
- `max_drawdown(equity_curve) -> float` — largest peak-to-trough drop as a fraction (0..1).
- `sharpe(equity_curve, periods_per_year) -> float` — mean/stdev of per-step simple returns,
  annualised by `sqrt(periods_per_year)`; 0.0 if <2 points or zero stdev. Risk-free rate 0.
- `avg_win(trades) / avg_loss(trades) -> float`
- `periods_per_year(interval) -> float` — derived from `CANDLE_INTERVAL_MS`
  (e.g. 1m → 525_600).

## 8. Backtester — `backtest.py`

- `run_backtest(skill, candles, cfg, starting_balance, window=200) -> dict`
  Builds a `MemoryLedger`, a `Broker(cfg.fee, cfg.slippage)`, then iterates the candles:
  for each index `i` from 1..len, calls
  `engine.process_tick(ledger, broker, [skill], candles[max(0, i-window+1):i+1], cfg)`.
  Skills self-guard their warm-up (return HOLD until enough candles), so no separate warm-up
  handling is needed. After the loop, computes the metrics from §7 and returns a dict:
  `{strategy, trades, final_equity, return_pct, win_rate, profit_factor, max_drawdown,
  sharpe, avg_win, avg_loss, equity_curve}`.
- `main()` — CLI via `argparse`:
  - `--skill NAME` (optional; default: all of `cfg.enabled_skills`)
  - `--days N` (default 30) · `--interval STR` (default `cfg.interval`)
  - `--source {all,live,history}` (default `all`)
  - `--balance F` (default `cfg.starting_balance`)
  - `--csv PATH` (optional; writes the equity curve of the single backtested skill, or per
    skill when multiple, as `time,strategy,equity`)
  Flow: open `Database`; `ensure_history(...)` for the pair/interval/days; for each skill,
  `get_candles_range(..., source)`, `run_backtest(...)`, collect results; print a leaderboard
  table sorted by `return_pct` (or `sharpe`) plus the honesty reminder; optionally write CSV.

`now_ms` for `ensure_history` is taken once in `main()` via `int(time.time()*1000)`.

## 9. Reporting

`backtest.main` prints a table: `strategy | trades | return% | sharpe | maxDD% | win% | PF`,
sorted best-first, followed by the one-line reminder:
`Note: backtest results can overfit — confirm with live paper trading before going live.`
`report.py` (Phase-1 leaderboard) is unchanged.

## 10. Components / file map

```
homing_trade/
├── db.py        # + candles table & methods (modify, additive)
├── engine.py    # + save live candles in run() (modify, additive)
├── history.py   # NEW — gap-aware backfill + paging
├── ledger.py    # NEW — MemoryLedger
├── metrics.py   # NEW — pure metric functions
└── backtest.py  # NEW — run_backtest + CLI
```

## 11. Testing

- `metrics.py`: unit tests with hand-computed expected values (return, win rate, profit
  factor, drawdown on a known curve, Sharpe on a known series, periods_per_year).
- `ledger.py`: round-trip tests (balance, open/get/close position, trade & equity capture);
  a test asserting it satisfies what `process_tick` calls.
- `db.py` candles: save/upsert idempotency (same time twice → one row, values updated),
  range query with source filter, bounds/count.
- `history.py`: with an injected fetcher returning a fixed series — first call fetches &
  stores, second call (data present) makes **zero** fetcher calls; gap-fill only fetches the
  missing span; mid-paging error returns stored data without raising.
- `backtest.py`: `run_backtest` over a synthetic trending series produces a plausible result
  dict with the right keys and a non-empty equity curve; result is **identical** when run
  twice (determinism); a flat series yields ~no trades. No live network in any test.
- `engine.py`: existing tests still pass; add one asserting `run()` persists fetched candles
  to the `candles` table with `source='live'`.

## 12. Out of scope (later phases)

Parameter optimisation / grid search (a possible Phase 2.5), walk-forward analysis, ML/RL
skills (Phase 3), live trading (Phase 4), multi-pair backtesting.

## 13. Risks

- **Volume of 1m candles:** 90 days ≈ 130k candles ≈ 130 paged calls on first backfill.
  Acceptable one-time cost; cached thereafter. Larger intervals (5m/1h) drastically reduce
  it and are supported.
- **Forming last candle:** a live fetch may include the still-forming candle; the upsert
  (`DO UPDATE`) corrects it on the next fetch, so the store converges to final values.
- **Overfitting:** inherent to backtesting — mitigated by the paper-first rule and the
  printed reminder, not by code.
