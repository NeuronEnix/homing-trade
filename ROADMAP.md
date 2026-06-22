# ROADMAP — homing-trade

Autonomous crypto-futures paper-trading bot (Python 3.12, stdlib-first; optional deps: `anthropic`, `requests`).

**North star:** profitability — but NOT a near-term gate. Right now the order is: clean structure → total observability → UI/management/always-on → the autonomous learn→correct loop. Everything is paper-money by default; `LiveBroker` stays `dry_run` until an explicit human gate.

How to read this file: phases are in priority order (Phase 1 = most urgent). Check boxes as tasks land. Keep the `Progress:` line accurate so the GitHub board and this file agree. The user will not review code line-by-line and may merge on trust, so every PR must keep tests green and be fully self-explanatory.

---

## Phase 1 — Structural foundation (kill the god-files, harden module boundaries)
Goal: make the autonomous loop safe to operate and later self-modify by giving every concern a sharp module boundary.

- [x] Introduce a `repository.py` layer that wraps `db.py`: typed read/write methods so `engine.py`, `web.py`, `report.py`, `backtest.py`, `daemon.py` stop importing `Database` and issuing raw SQL directly (audit flagged this 5-way coupling). _(repository.py wraps Database; `report.py` + `engine.run` + `backtest.py` + `web.py` migrated; only `repository.py` imports `Database` now — `daemon.py` never used raw SQL)_
- [x] Define a `Ledger` ABC (new `ledger_base.py`) and make both `ledger.MemoryLedger` and the SQLite-backed repository implement it, replacing the current duck-typed interface. _(ledger_base.Ledger; MemoryLedger + Repository both implement it)_
- [x] Decompose `engine.py` (228 lines, fan-out 9): extract `SkillRunner` (build/run skills), `PositionManager` (`_open_position`/`_close_position`/stop/liquidation), and keep `engine.run` as a thin orchestration loop. `process_tick` should call into these, not inline the logic. _(PositionManager (open/close/manage_risk) + Advisor (sizing) + SkillRunner (build/run skills + trade alerts); engine.run is now a thin fetch/sleep loop)_
- [x] Split decision-logic (`Strategy.on_candle` → action+confidence) from execution-plumbing (leverage, `risk_pct`, `stop_pct`, sizing) so a skill never reaches into `cfg` for sizing; introduce a small `Advisor`/sizing helper called by `PositionManager`. _(advisor.Advisor owns sizing; PositionManager.open uses it)_
- [x] Extract the embedded HTML in `web.py` (335 lines) into a `web_assets/` template + a thin `build_state()` that calls the repository, not raw queries. _(DASHBOARD_HTML → web_assets/dashboard.html, loaded at import; build_state already on Repository)_
- [x] Add `PRAGMA`/index review in the repository: indexes on `decision_log(strategy, ts)`, `llm_responses(strategy, ts)`, `trades(strategy, ts)`, `trades(position_id)` for cheap reflection joins (keep existing WAL). _(db.py MIGRATIONS v1)_
- [x] Add `schema_version` to the `state` table + a tiny forward-only `migrate()` run at `Database.__init__`; every schema change bumps it (foundation for safe self-modification later). _(db.py `_migrate()` + `SCHEMA_VERSION`)_
- [x] Keep all of the above behind passing tests: extend `test_engine.py`, `test_db.py`, `test_web.py`; add `test_repository.py`. _(added test_repository, test_ledger_base, test_advisor, test_position_manager, test_skill_runner; extended test_db/test_web — full suite green throughout)_

Progress: 8/8

---

## Phase 2 — Total observability (complete SQLite action ledger + read-only self-query layer)
Goal: every action the bot takes (signal, open, close, fill, fee, P&L, AI rationale, error, risk veto) is one queryable row; the AI can ask "how did I do" through a safe read-only layer.

- [ ] Extend `decision_log` with: `decision_id TEXT` (UUID), `prompt_version TEXT`, `playbook_version TEXT`, `regime TEXT`, `realized_vol REAL`, `intended_action TEXT`, `taken_action TEXT`, `rejection_rationale TEXT` (why a `DailyRiskGuard` veto blocked it). Tag at decision time, never re-derived. _(columns in v2; `decision_id`/`intended_action`/`taken_action`/`rejection_rationale` + `regime`/`realized_vol` now populated in process_tick; `prompt_version`/`playbook_version` pending the Phase-4 playbook system)_
- [ ] Add `prompt_hash` + a `prompt_version` to `llm_responses` and store `next_check_in_sec` / `requested_charts` the model returned, so a decision is fully replayable (the model already emits these in `llm_trader.py`). _(columns in v2; `next_check_in_sec` + `requested_charts` now populated in process_tick; `prompt_version`/`prompt_hash` pending the prompt/playbook system in Phase 4)_
- [x] Record fills/fees explicitly: ensure every `_open_position`/`_close_position` writes `fill price vs decision price` (slippage) and fee into `trades` (most fields exist; add `decision_price`/`slippage`). _(columns in v2; `PositionManager.open`/`close` now record `decision_price` + `slippage` on every fill, across all Ledger backends)_
- [x] Add a `risk_events` table written by `DailyRiskGuard` (`can_open` veto, kill-switch trip) so vetoes are observable, not silent. _(table + accessors in v2; veto wired in `PositionManager.open`, kill-switch trip wired in `engine.run`)_
- [x] Create a forward-only `regimes` table `(pair, interval, time, regime, adx, ema_slope, realized_vol)` computed at decision time; reuse/extend `_tf_summary` in `llm_trader.py` and a new ADX helper in `skills/indicators.py`. _(regimes table in migration v3; `classify_regime` computed once per tick in process_tick → upserted to `regimes` + each `decision_log` row tagged with regime + realized_vol)_
- [ ] Add a denormalized `trade_outcomes` view/table joining open→close `trades` by `position_id`, carrying `decision_id`, entry/exit price+ts, slippage, fees, `realized_pnl`, `pnl_pct`, `mae`, `mfe`, `holding_period_ms`, `exit_reason`, `regime_at_entry`, `prediction_correct`. This single row is what reflection reads. _(table v4 + `rebuild_trade_outcomes` builder (open→close join) + reader; core fields populated — entry/exit/ts/size/fees/slippage/realized_pnl/pnl_pct/holding_period. Enrichment pending: decision_id, regime_at_entry, mae/mfe, prediction_correct, exit_reason)_
- [ ] Enforce an outcome embargo: outcome columns carry `realized_at_ts`; the self-query layer only exposes them where `realized_at_ts <= now` (prevents the Oracle Fallacy). _(realized_at_ts populated + `trade_outcomes(as_of=...)` reader filters by it; selfquery integration pending)_
- [x] Build `selfquery.py`: a strictly read-only query API (no writes, parameterized, whitelisted tables) the AI calls for "win_rate / profit_factor / per-regime / per-variant" — wrap `metrics.py` (`sharpe`, `win_rate`, `profit_factor`, `max_drawdown`). _(read-only `SelfQuery`: win_rate/profit_factor/sharpe/drawdown/expectancy + risk-event counts + intended-vs-taken decision breakdown; per-regime/per-variant extend once regimes/trade_outcomes land)_
- [ ] Keep the audit-truth tables (`wallets`, `positions`, `trades`, `equity`, `candles`) machine-written only; only `decision_log`, `llm_responses`, `reflections`, `playbooks` may carry model-authored text (Hierarchy of Truth).
- [ ] Tests: `test_selfquery.py` (read-only enforced, embargo enforced), `test_trade_outcomes.py`, extend `test_db.py`/`test_llm_persistence.py`.

Progress: 4/10 _(v2–v4 schema; decision provenance + regime/vol tagging + risk_events + selfquery + llm replay fields + trade slippage + regimes table + trade_outcomes core (open→close join) with the realized_at_ts embargo reader all wired. Remaining: trade_outcomes enrichment (decision_id/regime/mae/mfe/prediction_correct/exit_reason), selfquery↔outcomes+embargo integration, Hierarchy-of-Truth doc, prompt-version tagging (Phase 4))_

---

## Phase 3 — UI visibility, full management, and always-on
Goal: a dashboard that shows per-strategy & per-AI leaderboard + the brain-log (saw/predicted/why) + a proposal/approval queue + every control, and a supervisor that survives restarts.

- [ ] Per-strategy & per-AI leaderboard in `web.build_state()`: balance, equity curve, win rate, profit factor, max drawdown, open position, last action — sourced from the repository, not raw queries.
- [ ] Brain-log panel: render `llm_responses` (observation / prediction / rationale / confidence / next_check_in_sec / error) per AI from `recent_llm_responses`.
- [ ] Per-regime and per-variant breakdown panel reading the Phase-2 `trade_outcomes`/`regimes` tables.
- [ ] Proposal/approval queue UI (reads Phase-4 `proposals` table): list pending param/prompt/playbook proposals with Approve / Reject buttons posting to a new `/api/proposal` endpoint (mirror the existing `/api/control` + `/api/close` pattern in `web.py`).
- [ ] Surface all controls already in `Controller` (start/stop/pause/resume/close_trade/reset) plus a per-AI enable/disable toggle.
- [ ] Daemon hardening: confirm `daemon.run_daemon` auto-restarts on crash with backoff (`daemon_backoff_seconds`), writes `daemon_status.json`, and the clean SIGTERM/SIGINT shutdown (already landed) is covered by `test_daemon.py`.
- [ ] Always-on: document + script an OS-level supervisor (launchd plist on macOS / systemd unit) so the daemon restarts on reboot; health-ping `#comms` via `comms.post` on start/stop/crash.
- [ ] Wire `comms.read` (bot-token inbound) so approvals can also arrive from Discord, not only the web UI (depends on the Discord bot token — see API shopping list).
- [ ] Tests: extend `test_web.py` for the new endpoints + approval queue; `test_daemon.py` for restart/backoff.

Progress: 0/9

---

## Phase 4 — The autonomous LEARN → CORRECT loop (the heart of the project)
Goal: the AI reflects over its own SQLite history, finds what went right/wrong, and PROPOSES adjustments; a human approves before anything applies. Nothing self-applies.

- [ ] Add tables: `reflections(id, strategy, kind['per_trade'|'periodic'], ts, batch_from_ts, batch_to_ts, trade_ids_json, metrics_json, lesson, new_playbook_version, model, raw)` and `playbooks(version PK, strategy, created_ts, rules_json, parent_version, retired_ts)` (append-only; never UPDATE a published version).
- [ ] Add `proposals(id, strategy, kind['param'|'prompt'|'playbook'|'strategy_toggle'], payload_json, rationale, status['pending'|'approved'|'rejected'], created_ts, decided_ts, decided_by, source_reflection_id)` — the gate between AI suggestion and applied change.
- [ ] Build `reflection.py`: a wall-clock-paced (decoupled from the candle loop, like the AI poll cadence) batched retrospection over `trade_outcomes`, producing a compact lesson + a proposed playbook diff. Use it as the PRIMARY loop.
- [ ] Add a per-closed-trade Reflexion pass (one extra LLM call at CLOSE) that critiques the original observation/prediction/rationale vs realized P&L and `prediction_correct`; store as a `per_trade` reflection (secondary loop — never the only mechanism).
- [ ] Score `prediction_correct` MECHANICALLY (did price do what `prediction` said over the horizon?), computed from candles — never by asking the model if it was right (reward-hacking guard).
- [ ] Implement playbook injection in `llm_trader._build_context`: inject the current bounded (top-K) playbook rules + bump `prompt_version`; refine (supersede stale rules), do not blindly append.
- [ ] Confidence calibration report: per-confidence-band realized win rate per strategy, feeding a proposed confidence floor (a `proposals` row).
- [ ] All proposals require human Approve (web UI Phase-3 or `#comms` reply); on approve, apply param/prompt/playbook change and record the new version. NEVER auto-touch risk limits / kill-switch / secrets / live-arming.
- [ ] Per-rule and per-playbook-version performance slope tracking; auto-surface (as a proposal) a rollback when a playbook version degrades. Disconfirmation guard: flag beliefs the bot stopped testing.
- [ ] Tests: `test_reflection.py` (batching, embargo respected, mechanical scoring), `test_proposals.py` (nothing applies without approval; protected fields can never be proposed).

Progress: 0/10

---

## Phase 5 — Multi-AI provider registry
Goal: any model behind `AI_<NAME>_*` env flags — whitelisted, each its own wallet+brain, with per-provider cost accounting.

- [ ] Generalize `ai_traders.build_ai_traders` from the two hard-coded brains (`llm_claude_code`, `llm_anthropic`) to a registry that discovers `AI_<NAME>_IS_ENABLED` / `AI_<NAME>_POLL_IN_SEC` / `AI_<NAME>_BACKEND` / `AI_<NAME>_MODEL` env flags.
- [ ] Backend adapters behind a common interface: `cli` (Claude CLI), `api` (Anthropic), plus OpenAI, Mistral, Llama/local — each optional, degrading to HOLD if its SDK/key is absent (mirror `llm_trader`'s never-crash contract).
- [ ] Whitelist enforcement: only registered/approved provider names spin up a wallet+brain.
- [ ] Per-provider cost accounting: a `cost_ledger(strategy, ts, model, prompt_tokens, completion_tokens, usd)` table; the leaderboard shows tokens and $ per provider.
- [ ] UI: per-AI cost column + enable/disable toggle (extends Phase-3 leaderboard).
- [ ] Tests: `test_ai_registry.py` (env discovery, whitelist, unknown/missing-key degrades cleanly).

Progress: 0/6

---

## Phase 6 — External research ingestion
Goal: news/sentiment/derivatives/on-chain signals into the AI context, and an internet scan that FILES new algorithms as candidate strategies (never auto-trading).

- [ ] `signals/fng.py`: Alternative.me Fear & Greed (free, no key) → cached table, injected into `llm_trader` context. Wire first.
- [ ] `signals/derivs.py`: Binance (then OKX/Bybit) public funding-rate + open-interest; aggregate cross-venue funding skew. No key needed.
- [ ] `signals/coindcx.py`: CoinDCX public futures funding/mark/orderbook for the actual traded instrument (source of truth).
- [ ] `signals/price_ref.py`: CoinGecko Demo reference price/market context to sanity-check CoinDCX (needs Demo key).
- [ ] Optional `signals/news.py`: RSS-into-LLM as the default news feed (CryptoPanic free tier retires 2026-04-01; do not hard-depend on it).
- [ ] Cache all external pulls in SQLite with `fetched_at`; respect rate limits; every fetch path degrades to "signal unavailable" without crashing the loop.
- [ ] Candidate-strategy intake: an internet/research scan that writes new algorithm ideas as `proposals(kind='strategy_toggle')` rows for human approval — never auto-enabled.
- [ ] Tests: `test_signals_*` with injected fetchers (offline, deterministic).

Progress: 0/8

---

## Phase 7 — Profitability via honest walk-forward backtests (continuous)
Goal: continuous, honest walk-forward evaluation. It is OK to lose paper money while learning; the discipline is what matters.

- [ ] Walk-forward harness on top of `backtest.run_backtest`: train window → freeze params/prompt/playbook → evaluate on the next unseen window → roll forward. A change is "learned" only if it holds out-of-sample.
- [ ] Add the shortlisted strategies as skills (see candidate algos): Supertrend, volume-confirmed breakout, TTM Squeeze, regime filter, z-score reversion — each with its own `test_<skill>.py`.
- [ ] Regime filter as a gate/weight feeding the allocator + committee (highest portfolio-level leverage).
- [ ] A/B variant bookkeeping: `experiments(id, hypothesis, variant_a, variant_b, metric, mde, start_ts, end_ts, n_a, n_b, result, p_value, correction_method)`; record search budget for honest multiple-testing accounting.
- [ ] Promotion discipline: a variant is promoted only after it wins out-of-sample AND clears MDE with multiple-comparison correction (Bonferroni / BH) — surfaced as a `proposals` row, human-approved.
- [ ] Guard against the LLM "profit mirage": only trust backtests on post-cutoff, walk-forward data.
- [ ] Continuous backtest job (cron/daemon) writing results to SQLite, surfaced in the UI.

Progress: 0/7

---

## Phase 8 — GitHub project-management visibility
Goal: the board, this ROADMAP, and the PRs always agree; PRs are self-explanatory because the user merges on trust.

- [ ] Mirror these phases onto a GitHub Project board (NeuronEnix/homing-trade); one card per task.
- [ ] Keep `Progress: x/N` lines accurate on every merge; CI check fails if ROADMAP boxes and board drift (optional later).
- [ ] PR template: what/why, what's tested, screenshots of UI changes, and an explicit "does NOT touch risk limits / kill-switch / secrets / live-arming" line.
- [ ] CI: run the full test suite on every PR; block merge on red.

Progress: 0/4

---

## Phase 9 — Self-modifying codebase (gated)
Goal: the AI proposes CODE changes as branches/PRs with backtests + passing tests; a human merges. Never touches protected zones without explicit sign-off.

- [ ] AI proposes code changes only as a branch + PR (via `gh`), with attached walk-forward backtest results and green tests.
- [ ] Hard guardrail: a protected-paths denylist (risk limits, kill-switch, secrets handling, live-arming, `LiveBroker` dry-run flag) that proposals can never modify.
- [ ] Every self-modification PR must pass CI before a human can merge; no auto-merge.
- [ ] Provenance: link each PR back to the `reflections`/`proposals` row that motivated it.

Progress: 0/4

---

## Phase 10 — Real-money test-drive milestone (opt-in, kill-switched)
Goal: after a paper track record, a tiny opt-in live run behind an explicit human gate, then gradual scale-up.

- [ ] Explicit human arming gate flips `live_enabled`/`live_dry_run` in `live_broker.py`; default stays dry-run.
- [ ] Tiny capital cap + hard `max_daily_loss` kill-switch verified live before any scale-up.
- [ ] Gradual scale-up only after a sustained paper + tiny-live track record.

Progress: 0/3

---

## Backlog / Later (low priority)
- [ ] Config versioning framework (`ConfigV1/V2` + migrator) — `config.py` has ~50 fields.
- [ ] `ErrorBoundary` abstraction (per-skill error counters, auto-disable after N failures).
- [ ] Decision/candle deterministic replay tool for trade-by-trade audit.
- [ ] Layered memory (short/mid/long/reflection) with temporal-decay retrieval, once trade count is large.
- [ ] Deferred algos needing new feeds/architecture: funding-rate skew (single-leg contrarian filter), rolling/anchored VWAP, CVD/order-flow (needs tick data), Ichimoku, BTC-ETH cointegration pairs (needs multi-leg positions).
- [ ] Paid data add-ons only if budget allows: Coinglass Hobbyist ($29/mo, aggregated funding + liquidation heatmaps).
- [ ] `StateSnapshot` versioned service for the UI if `build_state` schema churns.

---

_Design of record: `docs/superpowers/specs/2026-06-22-self-sustaining-trading-program-design.md`. Credentials to request: `docs/api-keys-shopping-list.md`. Two-way Discord approvals: `docs/discord-bot-setup.md`._
