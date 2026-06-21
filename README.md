# algo-trading — Paper Trading Strategy Lab

A **paper-trading** lab for crypto futures-style trading on **CoinDCX** price data.
Multiple strategies ("skills") trade isolated **virtual ₹5,000 wallets** against live
BTC/INR prices; a leaderboard shows which wins. Grows into a backtester, AI strategies,
and an automation layer with an opt-in (user-armed) live path.

> 💸 **Paper-first. No real money. No API keys. No live orders** unless *you* deliberately
> arm the live adapter with your own keys. This is a learning-and-research tool first.

## Status — all four phases complete (128 tests)

| Phase | What it adds |
|-------|--------------|
| **1 — Core** | Engine, SQLite, MA-trend / RSI / Grid skills, paper broker (leverage, fees, stop, liquidation), tournament leaderboard, decision logging |
| **2 — Lab** | Persistent candle store (history + live, one table); gap-aware history backfill; backtester reusing the live execution path; metrics (return, Sharpe, drawdown, profit factor, win rate) |
| **3 — AI** | Tabular Q-learning RL strategy (persists across runs); Bull/Bear/Risk-Supervisor committee (offline heuristic default, optional Claude-backed mode off by default); meta-allocator routing capital to proven strategies |
| **4 — Automation** | Pluggable alerts (console/file/webhook); daemon supervisor (heartbeat, restart, lifecycle alerts); per-trade alert hook; **guarded** CoinDCX live-order adapter (dry-run default, HMAC-signed, never auto-armed) |

Specs: `docs/specs/`. Plans: `docs/superpowers/plans/`.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m algotrading.engine     # run the paper tournament loop
python -m algotrading.report     # leaderboard
python -m algotrading.backtest --days 90        # backtest all strategies on stored history
python -m algotrading.daemon     # run the paper bot unattended (heartbeat + alerts)
```

## Live trading (opt-in, user-armed only)

`algotrading/live_broker.py` is a complete CoinDCX order adapter, but it is **dry-run by
default and never wired into the automated bot**. Going live is a deliberate action *you*
take: set your keys in the environment (`COINDCX_API_KEY` / `COINDCX_API_SECRET`), construct
`LiveBroker(api_key=..., api_secret=..., dry_run=False)` yourself, and route signals to it.
The paper engine and daemon never import it. Backtest → paper-prove → only then consider live.

## Tests

```bash
python -m pytest -q     # 128 tests
```
