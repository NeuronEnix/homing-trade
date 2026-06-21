# homing-trade — Paper Trading Strategy Lab

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

python -m homing_trade.engine     # run the paper tournament loop
python -m homing_trade.report     # leaderboard
python -m homing_trade.backtest --days 90        # backtest all strategies on stored history
python -m homing_trade.daemon     # run the paper bot unattended (heartbeat + alerts)
```

## Live trading (opt-in, user-armed only)

`homing_trade/live_broker.py` is a complete CoinDCX order adapter, but it is **dry-run by
default and never wired into the automated bot**. The paper engine and daemon never import it.

**Where your keys go.** Copy the template and fill in your CoinDCX keys — `.env` is
gitignored, so your real keys are never committed or pushed:

```bash
cp .env.example .env        # then edit .env: paste COINDCX_API_KEY / COINDCX_API_SECRET
```

Get keys from CoinDCX → Profile → API Dashboard. **Only needed for live trading** — paper
trading, backtesting, and the daemon need nothing here.

**Arming live (a deliberate action you take):**

```python
from homing_trade.live_broker import LiveBroker
lb = LiveBroker.from_env()                 # reads .env; dry_run=True (still simulated)
lb = LiveBroker.from_env(dry_run=False)    # ⚠️ THIS places REAL orders with your money
```

`from_env()` defaults to `dry_run=True`, so even after adding keys nothing is live until you
pass `dry_run=False` yourself. Backtest → paper-prove → only then consider live, small.

## Telegram alerts (optional)

Get a phone ping on every (paper) trade and on daemon start/stop/crash.

**1. Create a bot** — open Telegram, message **@BotFather**, send `/newbot`, follow the
prompts. It replies with a **token** like `123456789:AAE...`.

**2. Get your chat id** — message your new bot anything (say "hi"), then visit
`https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and copy the number in
`"chat":{"id": ...}`. (Or message **@userinfobot**, which just tells you your id.)

**3. Put them in `.env`** (gitignored — never committed):
```bash
HOMING_ALERT_MODE=telegram
TELEGRAM_BOT_TOKEN=123456789:AAE...
TELEGRAM_CHAT_ID=123456789
```

**4. Run the daemon** — it reads `.env`, switches to Telegram, and pings you on every trade:
```bash
python -m homing_trade.daemon
```

Alerts never crash the bot — if Telegram is unreachable, the trade still happens and the
error is swallowed. To go back to terminal output, remove `HOMING_ALERT_MODE` (default is
`console`).

## Tests

```bash
python -m pytest -q     # 138 tests
```
