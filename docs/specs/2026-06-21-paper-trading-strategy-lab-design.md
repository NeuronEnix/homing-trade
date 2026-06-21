# Paper Trading "Strategy Lab" — Design Spec

- **Date:** 2026-06-21
- **Status:** Approved (design); implementation pending
- **Location:** `/Users/krb/adoc2/rnd/algo-trading/`
- **Owner:** devansh@jum.bz

## 1. Goal & Philosophy

Build a **paper-trading strategy lab** for crypto futures-style trading on CoinDCX
price data. Multiple strategies ("skills") each trade an isolated virtual wallet
against the *same* live market feed, and a leaderboard shows which performs best.

Core principles:

- **Paper-first, always.** No real money, no API keys, no live orders until a
  strategy has proven itself on virtual currency and the user explicitly opts in.
- **Tournament.** Strategies compete head-to-head on equal virtual capital so we
  can compare them fairly on real CoinDCX prices.
- **Build it up.** Start with simple, understandable rule-based strategies; grow
  toward ML/RL and multi-agent AI "rules" without rearchitecting.
- **Traceable decisions.** Every trade records *why* it happened (which signal,
  what indicator values). Good practice and aligned with 2026 AI-trading norms
  ("traceable decision chains", named accountable human).

End goal: a validated, fully-automated trader — reached in phases, not in one leap.

## 2. Key Realities (decisions baked into the design)

- **Currency:** Everything is denominated in **INR (₹)**. Virtual wallet, P&L,
  ledger, leaderboard — all ₹.
- **"INR futures" caveat:** CoinDCX's *real* futures are USDT-margined; their INR
  markets are spot-only. For the paper bot we pull **live BTC/INR prices** from
  CoinDCX and **simulate futures-style mechanics** (leverage, long *and* short) on
  top, with books kept in INR. Going live would require revisiting this.
- **Virtual capital:** **₹5,000 per strategy** (paper). Configurable.
- **Pair:** **BTC/INR** to start (configurable; ETH/INR trivially added).

## 3. Scope

**In scope (Phase 1):** live data feed, paper broker, 3 rule-based strategies,
tournament engine, SQLite persistence, decision logging, CLI leaderboard/report.

**Out of scope (later phases):** backtester, ML/RL strategies, multi-agent AI
layer, live trading, web UI, alerts/notifications.

## 4. Architecture

Small, single-responsibility modules. Python 3.12, standard library + minimal deps.

```
algo-trading/
├── algotrading/
│   ├── config.py        # all tunables in one place
│   ├── feed.py          # CoinDCX candles/ticker -> clean OHLC
│   ├── db.py            # SQLite schema + read/write helpers
│   ├── broker.py        # virtual wallet, fills, fees, leverage, P&L, stops
│   ├── skills/
│   │   ├── base.py      # Strategy (skill) interface
│   │   ├── ma_trend.py  # MA crossover (trend-following)
│   │   ├── rsi_revert.py# RSI mean-reversion
│   │   └── grid.py      # grid trading
│   ├── engine.py        # main loop: fetch -> run skills -> execute -> persist
│   └── report.py        # leaderboard + per-strategy stats
├── data/                # sqlite db file lives here (gitignored)
├── tests/
├── docs/specs/
└── README.md
```

### 4.1 Data flow

```
CoinDCX candles API  ->  feed.py  ->  engine.py
                                        |  (latest candle + rolling window)
                                        v
                         for each skill: skill.on_candle() -> Signal
                                        |
                                        v
                         broker.execute(skill_wallet, signal) -> Fill
                                        |
                                        v
                         db.py  (trades, positions, equity, decision_log)
                                        |
                                        v
                                   report.py (leaderboard)
```

## 5. Strategy ("skill") interface

Decision-source-agnostic so a skill can be a rule, an ML model, or an LLM agent later.

```python
# skills/base.py
@dataclass
class Signal:
    action: str        # "LONG" | "SHORT" | "CLOSE" | "HOLD"
    confidence: float  # 0..1 (used later for sizing/allocation)
    reason: str        # human-readable: indicator values, why (decision log)

class Strategy:
    name: str
    def on_candle(self, candles: list[Candle], position: Position | None) -> Signal: ...
```

- `candles`: rolling window of recent OHLCV (newest last).
- `position`: the skill's current open position on its wallet (or None).
- Pure function of inputs → easy to unit-test with fixed candle fixtures.

## 6. Seed strategies (Phase 1 rules)

All parameters live in `config.py`.

1. **MA-crossover trend (`ma_trend`)** — fast EMA(9) vs slow EMA(21) on 1m candles.
   - Fast crosses above slow → `LONG`; crosses below → `SHORT` (or `CLOSE` if shorts off).
2. **RSI mean-reversion (`rsi_revert`)** — RSI(14).
   - RSI < 30 → `LONG`; RSI > 70 → `CLOSE`/`SHORT`. Optional Bollinger confirm.
3. **Grid (`grid`)** — define a price band and N levels around a reference price.
   - Buy a unit each level down, sell a unit each level up. Good in chop.

## 7. Paper broker model (`broker.py`)

Simulates futures-style execution realistically:

- **Wallet:** per-strategy ₹ balance, equity = balance + unrealized P&L.
- **Fills:** at the candle close price (Phase 1), adjusted by:
  - **Fee:** 0.05% taker per side (configurable).
  - **Slippage:** small configurable bps so paper P&L isn't rosy.
- **Leverage:** default **3x** (configurable). Position notional = margin × leverage.
- **Position sizing:** risk a fixed % of wallet per trade (default 2%), bounded by
  available margin.
- **Stop-loss:** per-trade SL (default 2% adverse move on price); auto-close.
- **Liquidation:** if unrealized loss consumes the position margin, force-close
  (simplified maintenance-margin model).
- **One position per skill at a time** in Phase 1 (grid is the exception: tracks
  multiple grid units, modeled as net position + open orders).

## 8. Data feed (`feed.py`)

Real CoinDCX public endpoints (no API key):

- **Candles:** `GET https://public.coindcx.com/market_data/candles?pair=I-BTC_INR&interval=1m&limit=200`
  - Returns `[{open, high, low, close, volume, time}, ...]`.
- **Ticker (fallback / latest price):** `GET https://api.coindcx.com/exchange/ticker`
  - Find `market == "BTCINR"` → `last_price`, `bid`, `ask`.
- **Market specs (validation):** `GET https://api.coindcx.com/exchange/v1/markets_details`.
- **Polling:** engine pulls latest candles every loop (default every 60s for 1m).
  De-dupe by candle `time`; only act on a newly-closed candle.
- **Resilience:** timeouts, retry with backoff, and skip-tick-on-failure (never
  crash the loop on a transient network error).

## 9. SQLite schema (`db.py`)

DB file: `data/paper_trading.db` (gitignored).

- **`strategies`** — `id, name, created_at, params_json, starting_balance`.
- **`wallets`** — `strategy_id, balance, equity, updated_at`.
- **`positions`** — `id, strategy_id, side, entry_price, size, leverage,
  margin, stop_price, opened_at, status` (open/closed).
- **`trades`** — `id, strategy_id, position_id, side, action, price, size, fee,
  pnl, ts` (one row per fill).
- **`equity`** — `id, strategy_id, equity, ts` (time series for the curve).
- **`decision_log`** — `id, strategy_id, ts, candle_time, action, confidence,
  reason, indicators_json` (the "traceable decision chain").
- **`state`** — `key, value` (engine cursor: last processed candle time, etc.) so
  restarts are safe and don't double-count.

## 10. Engine loop (`engine.py`)

```
load config + open db
ensure each strategy/wallet row exists (idempotent)
loop:
    candles = feed.get_candles(pair, interval, limit)
    if newest candle already processed: sleep; continue
    for skill in skills:
        pos = db.get_open_position(skill)
        signal = skill.on_candle(candles, pos)
        db.log_decision(skill, signal, candles[-1])
        broker.apply(skill_wallet, signal, candles[-1].close)  # may open/close/adjust
        db.record(trades/positions/equity)
    db.set_state("last_candle_time", newest.time)
    sleep(poll_interval)
```

Runs as a foreground process (`python -m algotrading.engine`); restart-safe via `state`.

## 11. Reporting (`report.py`)

`python -m algotrading.report` prints:

- **Leaderboard:** per strategy — equity, total return %, realized P&L, open P&L.
- **Metrics:** win rate, # trades, avg win/loss, profit factor, max drawdown.
  (Sharpe etc. come with the Phase-2 lab once we have enough history.)
- **Benchmark:** vs buy-&-hold BTC/INR over the same window.
- Optional `--strategy <name>` for a detailed trade ledger + recent decisions.

## 12. Configuration (`config.py`)

Single source of truth: pair (`I-BTC_INR` / `BTCINR`), interval (`1m`),
poll seconds (60), per-strategy starting balance (5000), leverage (3),
risk-per-trade (2%), stop-loss (2%), fee (0.05%), slippage bps, list of enabled
skills + their params. Optionally overridable via env vars.

## 13. Roadmap

| Phase | Adds | Status |
|-------|------|--------|
| **1 — Core** | engine, SQLite, 3 rule-based skills, tournament leaderboard, decision logging | now |
| **2 — Lab** | backtester over historical candles; metrics: Sharpe, drawdown, profit-factor; easy "add a skill" workflow | next |
| **3 — AI Rules** | RL skill (DQN-style, trains in the paper env); Bull/Bear/Risk-Supervisor multi-agent overlay; meta-allocator routing capital to proven winners | later |
| **4 — Automation** | daemon/scheduler, alerts, and — only on explicit opt-in — live trading with the user's keys | later |

## 14. Tech stack & dependencies

- Python 3.12 (already installed).
- `requests` (HTTP), `pytest` (tests). Indicators (EMA/RSI) implemented in plain
  Python/`statistics` to avoid heavy deps initially; add `pandas`/`numpy` only if
  the lab phase needs them.
- SQLite via the stdlib `sqlite3`.
- Virtualenv at `.venv/` (gitignored).

## 15. Testing approach

- Unit tests per skill with fixed candle fixtures → deterministic signals.
- Broker tests: fee/slippage math, P&L, stop-loss, liquidation edge cases.
- DB tests: schema round-trips, restart/state idempotency.
- Feed test: parse a recorded CoinDCX candles JSON sample (no live network in CI).

## 16. Risks & open questions

- **Min order size / precision:** real CoinDCX has min qty + price precision; paper
  mode can relax these but should mirror `markets_details` for realism. (Confirm in P1.)
- **1m candle latency:** the candles endpoint may lag a few seconds; acting on the
  last *closed* candle avoids using an incomplete bar.
- **Grid modeling complexity:** grid needs a small open-orders model; keep it simple
  in P1 (fixed band, static levels) and refine later.
- **Overfitting (the universal warning):** the whole point of paper-first is to catch
  strategies that only look good on paper. Don't tune to noise.
