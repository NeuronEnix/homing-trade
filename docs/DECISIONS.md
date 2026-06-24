# DECISIONS — homing-trade (ADR-lite log)

A human-legible record of the **product + architecture decisions** that aren't obvious from the code
or git history — *why* things are the way they are. Newest first. Each entry: **Decision · Why ·
Status**. Pairs with `ROADMAP.md` (the plan/state) and `docs/ARCHITECTURE.md` (the map). This is the
"what did we decide and why" memory; when a decision changes, add a new dated entry rather than
editing the old one.

---

## 2026-06-24 — The system was structurally long-only; fix reversal handling so it can short
**Decision.** A second look at `data/paper_trading.db` (now 29 closed trades, **all LONG**, net
−591 INR, every strategy negative) against the regime mix (`trend_down` 953 decisions vs `trend_up`
**50**) showed the loss was structural, not a reasoning failure: the market was overwhelmingly down
and the book could only go long. Root cause found in `engine.py`: a directional signal was acted on
**only when flat** (`position is None`) — an opposite-side signal while holding a position fell
through to a silent no-op. Mechanical trend strategies never emit `CLOSE`, so once `ma_trend` went
long on a brief up-crossover, a later bearish crossover could neither exit nor flip it; it rode the
downtrend to its stop (the correlated −2.15% stops). **Fix:** an opposite-side signal now always
**closes** the position (exit_reason `reversal`); when `reversal_flip_enabled` (default **true**) and
entries aren't paused, it then **opens the opposite side** so a trend strategy can actually short a
downtrend. Same-side signals are an explicit no-op. New flag `REVERSAL_FLIP_IS_ENABLED`.
**Why.** Closing on a self-reversal is unambiguously correct (your own strategy says the thesis is
gone). The flip is the directional lever that lets a long-only roster profit in a down-dominated
market — and the regime gate (enabled 2026-06-23) keeps flip whipsaw in chop size-limited. Paper
money, reversible by config, covered by tests (`tests/test_engine_reversal.py`).
**Status.** Live in code (904 tests green). Effective on the next daemon **restart** — the running
`web` process holds its old code+config. NOTE the learn loop is **not yet producing** (reflections=0,
proposals=0) despite `.env`; the running process predates the enablement → restart needed there too.
The **LLM brain's** long-bias is separate (it refuses longs in downtrends but doesn't yet short an
oversold extreme — sound for now); revisit via the reflect loop once it runs.

## 2026-06-23 — Data-driven tweaks after the first paper run
**Decision.** Reviewed `data/paper_trading.db` (1671 decisions, 9 closed trades). Findings: the bot
was **effectively long-only** (zero SHORT intents across the whole run — the LLM brain never shorted,
and the reverters are long-only by design) and took **three correlated −2.15% stops** holding LONG
into a `trend_down` regime; net P&L negative on a tiny sample. In response: (a) enabled the **regime
filter** (`REGIME_FILTER_IS_ENABLED=true`) — it scales a strategy's position size *down* when its
style mismatches the current regime (only ever reduces exposure); (b) enabled the **reflect→learn
loop** (`REFLECTION_IS_ENABLED=true`, model `sonnet`, local `claude` CLI = no API billing) so the
bot retrospects over its losses and files human-gated playbook proposals. Both are conservative and
paper-only; nothing auto-applies.
**Why.** The two mitigations built for *exactly* this failure mode were sitting disabled. The regime
gate addresses the mechanical reverters' bad longs immediately; the long-bias of the *LLM* brain is
style-neutral (the regime gate won't touch it) so correcting that is the learn-loop's job. Enabling
them is the literal "look at the data and tweak" ask.
**Status.** Live in `.env`. Effective on the next daemon restart (a running process keeps its old
config). Watch whether the LLM brain starts shorting / sizing down in downtrends; revisit if not.

**Decision (same day).** Several built features had **no operator switch** — `regime_filter`,
`allocator`, `committee_threshold`, `comms_inbound`, `continuous_backtest` were reachable only by
editing code. Wired them into `config.from_env` (`REGIME_FILTER_IS_ENABLED`, `ALLOCATOR_IS_ENABLED`,
`COMMITTEE_THRESHOLD`, `COMMS_INBOUND_IS_ENABLED`, `CONTINUOUS_BACKTEST_IS_ENABLED`, `TRUST_CUTOFF_ISO`).
**Why.** A feature you can't turn on without a code change isn't really shippable for a non-coding
operator. **Status.** Done; documented in `.env.example`.

## 2026-06-23 — Phase 11 starts on the paper feed (narrate-only) first
**Decision.** Build the human-in-the-loop control surface **paper-first**: ship the `#paper-trade`
narrate-only feed + the message contract (what/why/risk/`decision_id`/level) and a pure, fail-safe
escalation policy, all **default-OFF and zero-money**, before any interactive/live control.
**Why.** Prove the whole notification → escalation UX on the zero-risk channel; the live feed,
`ht-live` bot, owner-authenticated commands, and the risk-loosening ceremony only come up *after*
Phase 10 is armed. Some knobs (novelty thresholds, owner Discord ids, posture presets) are
deliberately **open decisions for the human** — see the Phase 11 spec §10.
**Status.** `trade_feed.py` + `escalation.py` landed (Phase 11 #1 done; #2 first cut). Live control
sequenced after Phase 10.

## 2026-06-23 — Project management lives in personal Linear; GitHub board retired
**Decision.** Roadmap/PM moved to **personal Linear** (workspace `kaushikrb`, team **KAU**, project
*homing-trade*): 12 milestones = the 12 phases, issues KAU-5..26 = remaining work, plus an
"Architecture & system map" document. The **GitHub Projects board was deleted**, and the
`tools/sync_board.py` + `tests/test_sync_board.py` that synced it were removed as dead tooling.
**Why.** The user wants a visual, human-facing tracker they own — and the work Linear MCP must never
be touched, so a separate personal workspace + its own MCP (`mcp__linear-personal__*`). The repo
markdown (ROADMAP + ARCHITECTURE + specs) stays the **canonical** source of truth (it's what the code
and a fresh chat read); Linear is the **human cockpit**, kept in sync by hand.
**Status.** Done. See the `docs-and-pm-system` memory for the cold-start order.

## 2026-06-22 — Direct-merge to main; checks run locally (no CI)
**Decision.** Commit straight to `main` (no PRs for the autonomous loop). The full test suite +
ROADMAP-consistency + a secret-guard run **locally** via `tools/check.sh` and a git pre-push hook
(`core.hooksPath tools/hooks`); the GitHub Actions CI workflow was **deleted**.
**Why.** CI was failing repeatedly (a brittle test pinned transient phase statuses) and added
friction for a solo, trust-merged project. The same checks run locally and before every push.
**Status.** Active workflow. (If collaborators ever join, revisit reinstating CI.)

## 2026-06-22 — Two-bot Discord architecture
**Decision.** Split Discord into two bots: **ht-dev** = the dev/proposal **control plane** (exists;
reads `#comms`, drives proposal approvals) and a future **ht-live** = the money **control plane**
(Phase 11; own token + Message Content Intent, scoped to `#live-trade` only, independently killable).
Trade feeds follow a `<word>-trade` naming convention: `#paper-trade` (narrate-only) and
`#live-trade` (interactive).
**Why.** Least-privilege: a leaked live token can never touch the code/proposal plane. **Status.**
ht-dev live; ht-live + `#live-trade` are Phase 11, behind the Phase-10 arming gate.

## Standing principles (set early, still in force)
- **Paper by default; real money is a separate, explicitly human-gated milestone (Phase 10).** The
  arming gate (`arming.py`) defaults to PAPER and *fails safe* (loud stop) for any live mode because
  live execution isn't wired yet. Building Phase 10 #1 armed *nothing*. Profitability is the north
  star but **not** a near-term gate — losing paper money while the loop learns is fine.
- **Hierarchy of Truth.** Audit-truth tables (wallets/positions/trades/equity/candles/cost_ledger/…)
  are **machine-written only**; only `decision_log`/`llm_responses`/`reflections`/`playbooks`/
  `proposals` may carry model-authored text. Enforced by a test that fails if a new table is
  unclassified.
- **`PROTECTED_PROPOSAL_FIELDS`.** Risk limits, leverage, kill-switch, fees/slippage, secrets/env
  names, live-arming, the trust cutoff, the regime-gate knobs (and the paper-feed toggle) can **never
  be proposed or applied** by the autonomous loop — re-asserted at both propose and apply time.
- **Self-modifying CODE (Phase 9) is DORMANT scaffolding.** The guardrail, gated proposer, no-merge
  policy, and provenance are built + tested but **wired to nothing**, and there is no code-author
  step — so the bot **cannot edit its own code today**, by design (rails before the actuator). This
  is distinct from the Phase-4 learn→correct loop, which is live but only alters **data** (params/
  playbooks), never code.
- **Autonomous loops are opt-in (default-OFF).** Reflection, research, the continuous backtest job,
  and Discord inbound approvals all default OFF — turning them on spends compute and/or mutates the
  approval gate, so it must be deliberate.
- **Claude Code brain runs on `sonnet`** (`AI_CLAUDE_CODE_MODEL=sonnet`) to cut cost; the global
  default model stays opus. UI-native multi-provider onboarding (add any LLM from the dashboard) is
  deferred to **Phase 12**.
- **stdlib-first.** Python 3.12; runtime deps are just `requests` + `pytest`; `anthropic` is optional
  and lazily imported. Every external feed degrades to "unavailable" rather than crashing the loop.
- **One persistence boundary.** Only `repository.py` touches `db.py`; consumers never issue raw SQL.
