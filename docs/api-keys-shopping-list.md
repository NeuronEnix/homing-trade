# API KEY / CREDENTIAL SHOPPING LIST

Free, no-key sources are wired without asking you for anything. Below is only what actually needs a credential, grouped by priority. Store everything in `.env` (gitignored) — never commit.

## P0 — needed for core loop & two-way control
- **`DISCORD_BOT_TOKEN` + `COMMS_CHANNEL_ID`** — unlocks INBOUND Discord (reading your approval replies). Webhooks are write-only, so `comms.read()` returns nothing until these are set. Required for approving proposals from Discord and for the learn→correct gate over chat. (See the Discord bot setup guide.) `COMMS_WEBHOOK_URL` (outbound, already supported) is separate and write-only.
- **`ANTHROPIC_API_KEY`** — unlocks the `llm_anthropic` brain (`AI_ANTHROPIC_IS_ENABLED`). The `llm_claude_code` (CLI) brain needs no key. Provide this if you want the API brain running alongside the CLI one for comparison.

## P1 — research ingestion (free tiers, just need a key)
- **CoinGecko Demo key** — free, 10k calls/mo, issued instantly. Unlocks global reference price/market context to sanity-check CoinDCX vs spot (`signals/price_ref.py`).
- **CoinDCX API key + secret** (`COINDCX_API_KEY` / `COINDCX_API_SECRET`) — public market data needs no key, but these unlock account/funding endpoints and are already required for any eventual live trading. Provide when convenient; not needed for paper.

## P2 — multi-AI registry (optional, only the providers you want)
- **`OPENAI_API_KEY`**, **`MISTRAL_API_KEY`**, or a local Llama endpoint — each unlocks an additional `AI_<NAME>_*` brain with its own wallet+brain and cost accounting. Add only the ones you actually want to compare.

## P3 — paid add-ons, only if you choose to spend
- **Coinglass Hobbyist ($29/mo)** — aggregated cross-exchange funding + liquidation heatmaps. Best signal-per-dollar paid option, but only after the free Binance/OKX/Bybit funding+OI is exhausted.
- **CryptoPanic paid** — only if its entry tier is cheap; the free tier retires 2026-04-01, so do not build a hard dependency.

## SKIP on this budget
CryptoQuant (no free API), Glassnode Professional (~$799/yr; free key is daily/delayed), LunarCrush ($90+/mo), Messari/Dune (niche), NewsAPI (24h-delayed, dev-only). Use RSS-into-LLM for news instead.
