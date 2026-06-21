# algo-trading — Paper Trading Strategy Lab

A **paper-trading** lab for crypto futures-style trading on **CoinDCX** price data.
Multiple strategies ("skills") each trade an isolated **virtual ₹5,000 wallet**
against the same live BTC/INR feed; a leaderboard shows which wins.

> 💸 **No real money. No API keys. No live orders.** Everything is simulated against
> live CoinDCX prices until a strategy proves itself and you explicitly opt into
> going live. This is a learning-and-research tool first.

## Status

Phase 1 (core engine + 3 rule-based skills) — in development.
See the design spec: [`docs/specs/2026-06-21-paper-trading-strategy-lab-design.md`](docs/specs/2026-06-21-paper-trading-strategy-lab-design.md).

## Roadmap

1. **Core** — engine, SQLite, MA-trend / RSI / Grid skills, tournament leaderboard.
2. **Lab** — backtester + metrics (Sharpe, drawdown, profit factor).
3. **AI Rules** — RL skill + Bull/Bear/Risk-Supervisor multi-agent overlay.
4. **Automation** — daemon, alerts, optional live trading (opt-in only).

## Quick start (once Phase 1 lands)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m algotrading.engine     # run the tournament loop
python -m algotrading.report     # see the leaderboard
```
