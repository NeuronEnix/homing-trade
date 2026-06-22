# homing-trade — Paper Trading Strategy Lab

A **paper-trading** lab for **CoinDCX INR-margin futures** (the BTC/USDT perpetual,
margined in ₹ — no spot, no options). Multiple strategies ("skills") trade isolated
**virtual ₹5,000 wallets** with **15× leverage**; a leaderboard shows which wins. Includes
a backtester, AI strategies, an automation/alerts layer, daily risk controls + a kill
switch, and an opt-in (user-armed) live path.

> 💸 **Paper-first. No real money. No API keys. No live orders** unless *you* deliberately
> arm the live adapter with your own keys. This is a learning-and-research tool first.

## Status — all four phases complete (203 tests)

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

python -m homing_trade.web        # ⭐ browser dashboard + controls (runs the bot too)
python -m homing_trade.engine     # run the paper tournament loop (headless)
python -m homing_trade.report     # leaderboard
python -m homing_trade.backtest --days 90        # backtest all strategies on stored history
python -m homing_trade.daemon     # run the paper bot unattended (heartbeat + alerts)
```

## Web dashboard

`python -m homing_trade.web` opens a control center at **http://localhost:8787** (stdlib HTTP
server — no Flask/React) and runs the engine in a background thread. Use it *instead of* the
bare daemon (don't run both — they'd share one DB).

- **Live prices** for BTC / ETH / PAXG (configurable via `price_symbols`).
- **Every algo + AI brain**: equity, P&L %, open position, and the AI's latest
  *saw / predicts / why* reasoning (errors flagged).
- **Controls**: Start · Pause (stop new entries) · Resume · Stop · Reset (wipe ledger).
- **Exit any trade** at market with one click.
- **Logs**: recent trades + decision log, auto-refreshing every 2s.

## Risk controls & kill switch

Leverage and daily limits live in `homing_trade/risk.py` (separate from execution) and are
driven from `.env` — no code edits:

```bash
HT_LEVERAGE_MIN=1
HT_LEVERAGE_MAX=15        # the bot trades at the top of the band (no single leverage knob)
HT_MAX_TRADE_PER_DAY=0    # cap on INR notional opened per day (0 = no cap)
HT_MAX_DAILY_LOSS=0       # KILL SWITCH — halt trading once the day's loss hits this (₹)
HT_TRADING_ENABLED=true   # set false to STOP trading immediately
```

- **Kill switch:** once realized losses in a day reach `HT_MAX_DAILY_LOSS`, the bot stops
  opening trades, fires an alert, and the daemon halts. Limits reset the next day.
- **Master switch:** `HT_TRADING_ENABLED=false` stops new trades immediately.
- ⚠️ **15× is aggressive** — a ~6.7% adverse move liquidates a position. Set a sane
  `HT_MAX_DAILY_LOSS` before doing anything beyond paper.

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

## Discord alerts (recommended)

Get pinged on every (paper) trade and on daemon start/stop/crash. Discord needs **just a
webhook URL** — no bot token.

**1. Create a webhook** — in your Discord server: **Server Settings → Integrations →
Webhooks → New Webhook**, pick the channel, then **Copy Webhook URL**. It looks like
`https://discord.com/api/webhooks/<id>/<token>`.

**2. Put it in `.env`** (gitignored — never committed):
```bash
HT_ALERT_MODE=discord
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

**3. Run the daemon** — it reads `.env`, switches to Discord, and pings you on every trade:
```bash
python -m homing_trade.daemon
```

Messages look like: **💱 grid CLOSE** — `sell 0.001 @ 6,050,000 pnl=+₹40`.

Alerts never crash the bot — if Discord is unreachable, the trade still happens and the
error is swallowed. Remove `HT_ALERT_MODE` to go back to terminal output (default).

> Telegram is also supported (`HT_ALERT_MODE=telegram` with `TELEGRAM_BOT_TOKEN` +
> `TELEGRAM_CHAT_ID`) if you prefer it.

## AI traders — Claude decides entries (optional)

`homing_trade/skills/llm_trader.py` lets **Claude read the 1-minute + 15-minute charts and
decide *when* to trade** across multiple timeframes (15m/1h/4h bird's-eye by default, and it can request more — any interval up to day units, by count or ISO-UTC date range). Direction/timing only; size, leverage,
and the daily risk limits stay with the engine. Degrades to HOLD on any error.

There are **two independent brains** (`homing_trade/ai_traders.py`), each toggled and paced
on its own. Enable either, both, or neither — **if both are on, they run side by side with
separate wallets**, so you can compare them on the leaderboard:

| Brain | Strategy name | Backend | Needs |
|---|---|---|---|
| **Claude Code** | `llm_claude_code` | local `claude` CLI | nothing — rides your Claude Code subscription |
| **Anthropic** | `llm_anthropic` | Anthropic SDK | `ANTHROPIC_API_KEY` (cheaper/faster per call) |

```bash
# Brain 1 — Claude Code CLI (no API key)
AI_CLAUDE_CODE_IS_ENABLED=true
AI_CLAUDE_CODE_POLL_IN_SEC=3600
# Brain 2 — Anthropic API
AI_ANTHROPIC_IS_ENABLED=false
AI_ANTHROPIC_POLL_IN_SEC=900
ANTHROPIC_API_KEY=sk-ant-...
HT_LLM_MODEL=claude-opus-4-8    # model both brains use (claude-haiku-4-5 to cut cost)
```
⚠️ This is an **experiment, not a known edge** — there's no evidence an LLM reading short
timeframes beats the market. Paper-test it hard before trusting it.

## Tests

```bash
python -m pytest -q     # 203 tests
```
