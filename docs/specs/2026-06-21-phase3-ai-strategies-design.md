# Phase 3 — AI Strategies & Capital Allocation — Design Spec

- **Date:** 2026-06-21
- **Status:** Approved (design); implementation pending
- **Depends on:** Phases 1 & 2 (engine, broker, skills, db, backtester) — merged to `main`
- **Owner:** devansh@jum.bz

## 1. Goal

Add the "AI" layer the user asked for, in three self-contained pieces that all plug into
the existing `Strategy` / `process_tick` machinery so they work in both live paper trading
and backtesting:

1. **Reinforcement-learning skill** — a tabular Q-learning agent that *learns* from paper
   experience, no heavy ML deps.
2. **Multi-agent committee** — the Bull / Bear / Risk-Supervisor pattern from the research,
   implemented offline-first (deterministic, free, testable) with an *optional* Claude-backed
   mode (off by default).
3. **Meta-allocator** — routes capital toward strategies that have proven themselves, so the
   bot "builds itself up" over time.

Paper-first still holds: nothing here trades real money. The Claude-backed agents cost money
per call and need the user's API key, so they are disabled by default.

## 2. Constraints (carried from Phases 1–2)

- Python 3.12, stdlib + `requests` + `pytest`. The **only** new dependency is `anthropic`,
  and it is an **optional, lazy import** used solely by the Claude-backed agent mode — the
  core, tests, RL skill, heuristic agents, and allocator must run and pass with `anthropic`
  NOT installed.
- Currency INR; paper only; no API keys committed; `data/paper_trading.db` gitignored.
- New skills implement the existing `Strategy.on_candle(candles, position) -> Signal` interface
  and reuse `Broker`/`process_tick` unchanged.
- Commit-message trailer unchanged.

## 3. Reinforcement-learning skill (`skills/rl_qlearn.py`)

A `Strategy` that learns a policy by trial and error in the paper sandbox.

- **State (discretised, finite):** a tuple of `(rsi_bucket, trend_sign, position_state)`:
  - `rsi_bucket`: RSI(14) bucketed into {0:<30, 1:30–45, 2:45–55, 3:55–70, 4:>70}, or `-1`
    while warming up.
  - `trend_sign`: sign of `ema(closes, fast) - ema(closes, slow)` → {-1, 0, +1}.
  - `position_state`: {0: flat, 1: long}.
- **Actions:** {`HOLD`, `ENTER_LONG`, `CLOSE`} mapped to `Signal` actions (ENTER_LONG only
  fires when flat → LONG; CLOSE only when long; otherwise HOLD).
- **Reward:** change in mark-to-market equity attributable to this skill between the prior
  step and now (passed in by the learner each step), so profitable transitions are reinforced.
- **Learning:** standard Q-update `Q[s,a] += alpha*(r + gamma*max_a' Q[s',a'] - Q[s,a])`,
  ε-greedy action selection. Hyperparameters (`alpha`, `gamma`, `epsilon`) in config.
- **Interface fit:** `on_candle` is a *pure decision* given the current Q-table; the Q-table
  update happens through a small `RLLearner.observe(prev_state, action, reward, next_state)`
  helper that the engine/backtester calls. To keep `process_tick` unchanged, the RL skill
  stores its own `last_state`/`last_action` internally and updates on the next `on_candle`
  using the equity delta it reads from a callback. **Design decision:** the skill is
  self-contained — it computes its own reward from the candle close vs. its entry, so no
  engine change is required; `on_candle` both *learns from* the previous decision and *emits*
  the next one. The Q-table persists to `data/qtable_<name>.json` (gitignored) so it improves
  across runs; an in-memory table is used when no path is given (tests).
- **Determinism for tests:** ε and the RNG are injectable; tests run with ε=0 (greedy) and a
  seeded/fixed tie-break so behaviour is deterministic.

## 4. Multi-agent committee (`agents/` + `skills/committee.py`)

The Bull/Bear/Risk-Supervisor pattern as a pluggable agent framework.

### 4.1 Agent interface (`agents/base.py`)
```python
@dataclass
class AgentView:
    stance: str        # "BULLISH" | "BEARISH" | "NEUTRAL"
    confidence: float  # 0..1
    reason: str

class Agent(ABC):
    name: str
    def assess(self, candles: list[Candle], position: Position | None) -> AgentView: ...
```

### 4.2 Heuristic agents (`agents/heuristic.py`) — default, offline, free
- `BullAgent`: bullish when fast EMA > slow EMA and RSI rising / not overbought.
- `BearAgent`: bearish when fast EMA < slow EMA and RSI falling / not oversold.
- `RiskSupervisor`: independent of direction — returns NEUTRAL normally, but flags
  `stance="BEARISH"` (a veto) when recent volatility/drawdown is high (e.g. last-N candle
  range exceeds a threshold), modelling "don't trade into chaos".
All deterministic and unit-tested with fixed candle fixtures.

### 4.3 Claude-backed agents (`agents/llm.py`) — optional, OFF by default
- `LlmAgent(role, model=cfg.llm_model)` where `role ∈ {bull, bear, risk}`. On `assess`, it
  **lazily** `import anthropic`, builds a compact prompt (recent OHLC summary + indicators +
  role instruction), and calls `client.messages.create(model=..., output_config={...json_schema...})`
  constraining the reply to `{stance, confidence, reason}`. Default model **`claude-opus-4-8`**
  (configurable to a cheaper model for cost).
- **Robustness:** any exception (missing `anthropic`, missing API key, network/rate-limit,
  bad JSON) is caught and the agent returns `AgentView("NEUTRAL", 0.0, "llm unavailable: …")`
  — the committee degrades gracefully, never crashes. A module-level flag avoids re-importing.
- **Cost/honesty:** docstring + config note that this calls a paid API per decision and needs
  `ANTHROPIC_API_KEY`; it is never enabled unless the user sets `agent_mode="llm"`.

### 4.4 Committee skill (`skills/committee.py`)
A `Strategy` named `committee` that builds three agents (Bull, Bear, Risk) via a factory
keyed on `cfg.agent_mode` (`"heuristic"` default | `"llm"`), gathers their `AgentView`s, and
combines them into a `Signal`:
- **Risk veto:** if the Risk Supervisor is BEARISH (veto) → `CLOSE` if long, else `HOLD`.
- **Otherwise:** net = Bull.confidence(if BULLISH) − Bear.confidence(if BEARISH). Net > a
  threshold and flat → `LONG`; net < −threshold and long → `CLOSE`; else `HOLD`.
- The `Signal.reason`/`indicators` record each agent's stance for the decision log (the
  "traceable decision chain").

## 5. Meta-allocator (`allocator.py`)

Routes capital toward strategies that perform, so winners compound and losers shrink.

- `compute_allocations(perf: dict[str, float], *, floor=0.1, cap=1.0) -> dict[str, float]`
  Pure function. `perf` maps strategy name → a recent-performance score (e.g. recent return %).
  Returns a weight in `[floor, cap]` per strategy via a softmax-like normalisation, so every
  strategy keeps a minimum allocation (never fully starved) and the best gets the most.
- `recent_performance(db, strategy, lookback_trades=20) -> float` — reads the last N CLOSE
  trades' pnl from SQLite and returns their mean (0.0 if none).
- **Engine hook (config-gated, default off):** when `cfg.allocator_enabled`, the engine
  multiplies each skill's effective `risk_pct` by its allocation weight before sizing, so
  capital flows to winners automatically. Off by default keeps Phase-1/2 behaviour identical
  and all existing tests green. Implemented as a thin, well-tested helper so the change to
  `engine`/`backtest` sizing is minimal and reviewable.

## 6. Config additions (`config.py`)
`enabled_skills` may now include `"rl_qlearn"` and `"committee"`. New fields (all with safe
defaults): `agent_mode="heuristic"`, `llm_model="claude-opus-4-8"`, `rl_alpha=0.1`,
`rl_gamma=0.95`, `rl_epsilon=0.1`, `rl_fast=9`, `rl_slow=21`, `committee_threshold=0.2`,
`risk_vol_window=20`, `risk_vol_threshold=0.04`, `allocator_enabled=False`,
`allocator_lookback=20`, `qtable_dir="data"`.

## 7. Component / file map
```
homing_trade/
├── skills/rl_qlearn.py   # NEW — tabular Q-learning Strategy + RLLearner
├── agents/__init__.py    # NEW
├── agents/base.py        # NEW — Agent ABC + AgentView
├── agents/heuristic.py   # NEW — Bull/Bear/RiskSupervisor (offline)
├── agents/llm.py         # NEW — optional Claude-backed agents (lazy anthropic)
├── skills/committee.py   # NEW — Committee Strategy (agent factory + combine)
├── allocator.py          # NEW — compute_allocations + recent_performance
├── config.py             # MODIFY — new fields (additive)
└── engine.py / backtest.py  # MODIFY — optional allocator sizing hook (config-gated, default off)
```

## 8. Testing
- RL: state discretisation, Q-update math, greedy action selection (ε=0 deterministic),
  learns a trivial environment (reward shaping reinforces the profitable action), Q-table
  persistence round-trip.
- Heuristic agents: each agent's stance on fixed bullish/bearish/volatile fixtures; risk veto.
- Committee: risk-veto path, bull-dominant→LONG, bear-dominant→CLOSE, neutral→HOLD; agent
  factory returns heuristic agents by default.
- LLM agents: with `anthropic` absent (or an injected fake client), `assess` returns NEUTRAL
  without raising; with an injected fake client returning valid JSON, it parses to an AgentView.
  **No real network in tests.**
- Allocator: `compute_allocations` weights (winner > loser, all ≥ floor, sums sensible);
  `recent_performance` reads trades; engine hook scales `risk_pct` only when enabled.
- Backtest/engine: existing 83 tests stay green (new skills are opt-in via `enabled_skills`;
  allocator hook is default-off).

## 9. Out of scope (Phase 4)
Daemon/scheduler, alerts/notifications, and the live-trading adapter — all in Phase 4.

## 10. Risks
- **RL needs many episodes to learn** — on a 5k virtual wallet over limited candles it will be
  noisy; that's expected and educational. The value is the framework + persistence, not instant
  profit.
- **LLM agents cost money and are slow** per decision — hence off by default and only sensible
  at a slow cadence; the committee works fully offline for everyday use.
- **Allocator feedback loop** could over-concentrate; the `floor` guarantees every strategy
  keeps a minimum allocation.
