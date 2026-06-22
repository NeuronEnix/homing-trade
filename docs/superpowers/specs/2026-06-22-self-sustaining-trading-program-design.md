# DESIGN SPEC OUTLINE — homing-trade autonomous loop

## 0. Scope & invariants
- Paper-money by default; `LiveBroker` stays `dry_run=True` until an explicit human arming gate.
- stdlib-first Python 3.12; only optional deps `anthropic`, `requests`. Every external/LLM path degrades to a safe default (HOLD / "unavailable") and never crashes the engine loop.
- Hierarchy of Truth: deterministic audit-truth state (`wallets`, `positions`, `trades`, `equity`, `candles`) is machine-written only and always overrides model-stated beliefs. The LLM may only author rows in `decision_log`, `llm_responses`, `reflections`, `playbooks`, `proposals`.
- Never commit `.env`/`data`/secrets. Public repo.

## 1. Module architecture (target)
- **Persistence:** `db.py` (raw SQLite + schema + migrations) → wrapped by new `repository.py` (typed, parameterized, whitelisted). `ledger_base.py` defines a `Ledger` ABC implemented by both the repository and `ledger.MemoryLedger`.
- **Execution core:** decompose `engine.py` into `SkillRunner` (build+run skills), `PositionManager` (open/close/stop/liquidation, sizing via an `Advisor`), and a thin `engine.run` loop. `process_tick` orchestrates only.
- **Decision vs plumbing:** `Strategy.on_candle` returns action+confidence only; sizing/leverage/stop live in `PositionManager`/`Advisor`, not in the skill.
- **Risk:** `risk.DailyRiskGuard` unchanged in spirit; now also writes `risk_events`.
- **AI:** `skills/llm_trader.LlmTrader` (per-brain) + `ai_traders` registry; backend adapters behind one interface.
- **Learning:** new `reflection.py`, `selfquery.py`; tables `reflections`, `playbooks`, `proposals`, `experiments`, `cost_ledger`.
- **Surfaces:** `web.py` (dashboard + controls + approval queue), `daemon.py` (always-on supervisor), `comms.py` (Discord two-way), `notify.py` (alerts).
- **Signals:** new `signals/` package (fng, derivs, coindcx, price_ref, news).

## 2. Data model / schema evolution
- `state.schema_version` + forward-only `migrate()` at `Database.__init__`.
- Extend `decision_log`: `decision_id`, `prompt_version`, `playbook_version`, `regime`, `realized_vol`, `intended_action`, `taken_action`, `rejection_rationale`.
- Extend `llm_responses`: `prompt_hash`, `prompt_version`, `next_check_in_sec`, `requested_charts`.
- Extend `trades`: `decision_price`, `slippage`.
- New: `risk_events`, `regimes` (forward-only), `trade_outcomes` (denormalized join, embargoed outcome columns with `realized_at_ts`), `reflections`, `playbooks` (append-only), `proposals`, `experiments`, `cost_ledger`.
- Indexes: `decision_log(strategy, ts)`, `llm_responses(strategy, ts)`, `trades(strategy, ts)`, `trades(position_id)`.

## 3. Observability & self-query layer
- Every action → exactly one row (signal/open/close/fill/fee/pnl/rationale/error/veto).
- `selfquery.py`: read-only, parameterized, table-whitelisted; exposes `metrics.py` aggregates (sharpe/win_rate/profit_factor/max_drawdown) broken down by regime and variant; refuses any write; enforces the outcome embargo (`realized_at_ts <= now`).

## 4. Learn → correct loop
- **Primary:** batched periodic retrospection over `trade_outcomes` on a wall-clock cadence (decoupled from the candle loop). Output: a compact lesson + proposed playbook diff (top-K, supersede not append).
- **Secondary:** per-closed-trade Reflexion (one extra LLM call at CLOSE) critiquing observation/prediction/rationale vs realized P&L.
- **Scoring:** `prediction_correct` computed mechanically from candles; reward = realized P&L/Sharpe from the audit ledger, never model self-assessment.
- **Gate:** all changes become `proposals` rows; nothing applies without human Approve (web UI or `#comms` reply). Protected zone (risk limits / kill-switch / secrets / live-arming) can never be proposed.
- **Drift control:** per-rule + per-playbook-version performance slope; auto-surface rollback proposals; disconfirmation guard for untested beliefs.
- **Validation:** any proposed param/prompt/playbook change is walk-forward / out-of-sample gated before promotion; MDE + multiple-testing correction; record search budget.

## 5. UI & always-on
- `build_state()` (via repository): per-strategy & per-AI leaderboard, brain-log, per-regime/variant breakdown, proposal queue, cost-per-provider.
- Controls: existing `Controller` (start/stop/pause/resume/close_trade/reset) + per-AI toggle + Approve/Reject endpoints (`/api/proposal`).
- Daemon: crash auto-restart with backoff, `daemon_status.json`, clean SIGTERM/SIGINT; OS-level supervisor (launchd/systemd) for reboot survival; `#comms` health pings.

## 6. Multi-AI registry & cost accounting
- Discover `AI_<NAME>_*` env flags → whitelisted brains, each its own wallet. Adapters: cli/api/openai/mistral/local, all optional, HOLD-on-missing. `cost_ledger` tracks tokens/$ per provider.

## 7. External research ingestion
- Free-first: Fear&Greed → derivs (Binance/OKX/Bybit) → CoinDCX (source of truth) → CoinGecko ref price → RSS news. Cached with `fetched_at`. Research scan files new algos as `proposals(kind='strategy_toggle')`.

## 8. Profitability
- Continuous walk-forward on `backtest.run_backtest`; new skills (Supertrend, vol-breakout, TTM squeeze, regime filter, z-score); A/B via `experiments`; promotion only out-of-sample + corrected.

## 9. Self-modifying code (gated) & live milestone
- AI proposes code as `gh` branch+PR with backtests + green CI; protected-paths denylist; human merges; PR links to motivating reflection/proposal.
- Live: explicit arming gate, tiny capital, verified kill-switch, gradual scale-up.

## 10. Testing & PM
- Every phase keeps the full suite green; new tests per new module. PRs self-explanatory (what/why/tested/protected-zone untouched). GitHub Project board mirrors phases; `Progress:` lines kept accurate.

## 11. Cross-cutting pitfalls to design against
Overfitting to noise (require sample size + MDE), temporal leakage / Oracle Fallacy (embargo), LLM price memorization / profit mirage (post-cutoff walk-forward only), reward hacking (mechanical scoring), hindsight bias (tag regime at entry), self-reinforcing error (disconfirmation guard), multiple-testing (record search budget + correction), context dilution (bounded playbook), editable ground truth (Hierarchy of Truth).

---

## Candidate strategies (shortlist)

Add as new `homing_trade/skills/*.py` with a `test_<skill>.py` each. Ordered by value-for-effort on 15m BTC perps.

1. **Supertrend (ATR trailing bands)** — low effort, high value: adaptive ATR-anchored trend-following that suits BTC volatility clustering; distinct from `ma_trend`/`donchian`; reusable as a trailing stop. Needs one new ATR helper in `skills/indicators.py`.
2. **Volume-confirmed breakout** — low effort, high value: upgrades the existing `donchian` breakout with a relative-volume gate (volume is already in `Candle`); the standard fakeout filter, ~25 lines.
3. **TTM Squeeze (Bollinger-in-Keltner)** — low/medium effort, high value: fills a real gap — no current skill detects volatility compression; reuses `bollinger()` + the new ATR; catches range→trend expansions common on 15m BTC.
4. **Regime filter / switching meta-layer (ADX + ATR/BB-width)** — medium effort, very high leverage: not a single signal but a gate that suppresses wrong-regime trades across the whole roster and feeds a regime label into the committee/RL and the observability layer. Biggest portfolio-level payoff.
5. **Z-score mean reversion** — lowest effort, modest marginal value: reuses Bollinger's mean/std; useful as a third reversion variant for the committee/allocator, but overlaps `rsi_revert`/`bollinger`. Fast-follow, not a headline add.

**Defer (need new feeds or multi-leg engine):** funding-rate skew (single-leg contrarian filter — needs a funding feed), rolling/anchored VWAP, CVD/order-flow (needs tick data), Ichimoku (overlaps trend family), BTC-ETH cointegration pairs (needs multi-leg positions the broker/`Position` model don't support yet).

---

## Open questions (need your decision)

1. Approval channel of record: should proposal approvals be authoritative from the web UI, from Discord (#comms), or both? If both, which wins on conflict, and does any approval need a second confirmation for higher-impact changes (e.g. enabling a new strategy vs tweaking a confidence floor)?
2. Multi-AI scope now vs later: do you want me to generalize ai_traders to the full AI_<NAME>_* registry in Phase 5 as planned, or keep just llm_claude_code + llm_anthropic until the learn->correct loop is proven? And which extra providers (OpenAI/Mistral/local Llama) do you actually intend to run?
3. Reflection cadence and cost: what wall-clock cadence for batched retrospection (daily? every N closed trades?), and is the per-closed-trade Reflexion (one extra LLM call per CLOSE) acceptable cost-wise on the API brain, or should it be CLI-only / sampled?
4. Minimum sample size before a proposal can be promoted: the research suggests ~60-90 trades per regime. Are you OK with the loop staying in 'observe only' until those samples accumulate, given current trade frequency at 15m?
5. Always-on host: is this running on your Mac (launchd) or a cloud/VPS host (systemd)? That decides which supervisor script and which geo considerations apply for Binance/OKX/Bybit public endpoints.
6. Budget for paid data/providers: is there a monthly cap (e.g. ~$100) I should design around, and is Coinglass Hobbyist ($29/mo) pre-approved if free derivs sources prove insufficient?
7. Self-modifying code (Phase 9): confirm the exact protected-paths denylist (risk.py limits, kill-switch, secrets handling, live_broker dry-run flag, live-arming) so the AI can never even PROPOSE edits there, only humans.
8. GitHub board: do you want me to actually create the Project board + issues now under NeuronEnix/homing-trade, or keep PM in ROADMAP.md only until you say go?

---

_Spec date: 2026-06-22 · Status: proposed (review via PR). Companion checklist: `ROADMAP.md`._
