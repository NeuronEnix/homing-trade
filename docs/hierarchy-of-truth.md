# Hierarchy of Truth

The single invariant that makes this bot safe to let learn from itself.

> **Model output may never write into the audit-truth record. Model prose lives only in its
> own quarantined tables. The bot's self-query layer can only read.**

As the system becomes self-improving (Phase 4) and eventually self-modifying (Phase 9), the
AI reads its own history to decide what to change. If the AI could also *write* into the
record it reads, two failures become possible:

- **Reward hacking** — the model marks its own predictions "correct", inflates a metric, or
  edits a losing trade's outcome, then "learns" from the flattering fiction it authored.
- **Oracle Fallacy / audit rot** — model narrative seeps into the ground-truth tables and you
  can no longer tell what *actually happened* (the fills, the P&L) from what the model *said*
  happened. The audit trail stops being trustworthy exactly when you most need it.

The Hierarchy of Truth prevents both by construction, not by good intentions.

## The two classes

Every table is in **exactly one** class. The classification is code, not just prose:
`AUDIT_TRUTH_TABLES` and `MODEL_AUTHORED_TABLES` in `homing_trade/db.py`.

### Audit-truth tables — machine-written ground truth

No model output may ever author or edit a row here. These are written only by deterministic
engine/broker code from real events or mechanical computation.

| Table            | What it holds                                              | Why it is ground truth |
|------------------|------------------------------------------------------------|------------------------|
| `strategies`     | Registry of strategy/AI names + their config               | Set by the operator/engine |
| `wallets`        | Per-strategy balances                                      | Moved only by recorded fills |
| `positions`      | Open positions                                             | Broker/engine state |
| `trades`         | Every fill: price, size, fee, P&L, slippage, exit_reason   | The financial record |
| `equity`         | Equity snapshots over time                                 | Computed from balance + unrealized |
| `candles`        | Market OHLCV                                                | External market data |
| `state`          | Engine cursors (e.g. `last_candle_time`)                   | Loop bookkeeping |
| `risk_events`    | `DailyRiskGuard` vetoes / kill-switch trips + reason       | Reasons are machine-generated guard strings, not model text |
| `regimes`        | Regime classification (ADX / EMA-slope / realized-vol)     | Computed mechanically at decision time |
| `trade_outcomes` | Denormalized open→close join + `prediction_correct`, MAE/MFE | Derived mechanically from `trades` + `candles` |

`prediction_correct` is scored **mechanically from prices** — never by asking the model
whether it was right. That is the whole point: the score lives in an audit-truth table and the
model cannot touch it.

### Model-authored tables — the only place model prose may live

These four tables may carry free model-authored text (observation / prediction / rationale /
lesson / playbook rule). They are inputs to reflection and explanation; they are **never**
treated as ground truth about what happened in the market or the account.

| Table           | Model-authored content                                          | Status |
|-----------------|-----------------------------------------------------------------|--------|
| `decision_log`  | `reason` (the strategy/LLM's stated reason for an action)       | live |
| `llm_responses` | `observation` / `prediction` / `rationale` / `raw`              | live |
| `reflections`   | Per-trade & periodic lessons over `trade_outcomes`              | Phase 4 (forward-declared) |
| `playbooks`     | Versioned, append-only rule sets proposed by reflection         | Phase 4 (forward-declared) |

Even within these tables, mechanical columns stay mechanical: `decision_log.confidence`,
`taken_action`, `rejection_rationale`, `regime`, `realized_vol` are written by code, not prose.

## The read path

`homing_trade/selfquery.py` is the AI's window into its own history ("how did I do?"). It is
**read-only**: it calls only read methods on the repository. (It holds a full repository
reference, which does technically expose writes, so this is enforced by test rather than
structurally prevented — `test_selfquery` and `test_hierarchy_of_truth` both assert the live
query surface touches only read methods, so a regression that reached for a write fails the
suite.) Look-ahead is additionally embargoed via `trade_outcomes.realized_at_ts` (`as_of`), so
reflection can't peek at outcomes that hadn't realized yet.

## Enforcement

This invariant is asserted by tests, so it can't quietly rot as the schema grows:

- `tests/test_hierarchy_of_truth.py`
  - **complete** — every live table is classified as audit-truth or model-authored (adding a
    table without classifying it fails the suite);
  - **disjoint** — no table is in both classes;
  - pins the audit-truth ground-truth set and the exactly-four model-authored set;
  - re-asserts that `SelfQuery` only ever calls read methods.
- `tests/test_selfquery.py` — the same read-only spy check on the live query surface.
- `tests/test_trade_outcomes.py` — `prediction_correct` and MAE/MFE are computed
  mechanically; the embargo (`as_of`) hides unrealized outcomes.

## Rules of thumb for future work

- Adding a table? Put its name in `AUDIT_TRUTH_TABLES` **or** `MODEL_AUTHORED_TABLES` in
  `db.py` (the test will remind you). Default to audit-truth unless it genuinely stores model
  free text.
- Need to store something the model said? It goes in a model-authored table — never as a
  column on `trades`/`positions`/`equity`/`candles`.
- Scoring whether a prediction came true? Compute it from `candles`/`trades`, write it to an
  audit-truth table. Never ask the model to grade itself.
- The self-query / reflection layer reads; a separate, deterministic write path records. Keep
  them apart.
