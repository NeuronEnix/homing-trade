# Phase 3: AI Strategies & Capital Allocation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a reinforcement-learning strategy, a Bull/Bear/Risk-Supervisor multi-agent committee (offline-first, optional Claude-backed), and a meta-allocator that routes capital to proven strategies — all plugging into the existing `Strategy`/`process_tick` machinery.

**Architecture:** New `Strategy` subclasses (`rl_qlearn`, `committee`) and a small `agents/` framework. The only new dependency is `anthropic`, used exclusively by an optional, lazy-imported, default-off LLM agent mode — everything else runs and tests pass with `anthropic` NOT installed.

**Tech Stack:** Python 3.12, stdlib + `requests` + `pytest`; optional lazy `anthropic`.

## Global Constraints

- Python 3.12. Core/tests must pass with `anthropic` NOT installed (it is lazy-imported only in `agents/llm.py`).
- Currency INR; paper only; no API keys committed; `data/` gitignored.
- New skills implement `Strategy.on_candle(candles, position) -> Signal` and reuse `Broker`/`process_tick` unchanged.
- Default Claude model for LLM agents is `claude-opus-4-8` (configurable). LLM mode is OFF by default (`agent_mode="heuristic"`).
- Run tests via `cd /Users/krb/adoc2/rnd/algo-trading && ./.venv/bin/python -m pytest <path> -v`.
- Commit after each task; every commit message ends with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

### Task 1: Config additions

**Files:**
- Modify: `algotrading/config.py`
- Test: `tests/test_config_phase3.py`

**Interfaces:**
- Produces new `Config` fields: `agent_mode="heuristic"`, `llm_model="claude-opus-4-8"`, `rl_alpha=0.1`, `rl_gamma=0.95`, `rl_epsilon=0.1`, `rl_fast=9`, `rl_slow=21`, `committee_threshold=0.2`, `risk_vol_window=20`, `risk_vol_threshold=0.04`, `allocator_enabled=False`, `allocator_lookback=20`, `qtable_dir="data"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_phase3.py
from algotrading.config import CONFIG


def test_phase3_defaults():
    assert CONFIG.agent_mode == "heuristic"
    assert CONFIG.llm_model == "claude-opus-4-8"
    assert CONFIG.rl_alpha == 0.1
    assert CONFIG.rl_gamma == 0.95
    assert CONFIG.rl_epsilon == 0.1
    assert CONFIG.committee_threshold == 0.2
    assert CONFIG.risk_vol_window == 20
    assert CONFIG.risk_vol_threshold == 0.04
    assert CONFIG.allocator_enabled is False
    assert CONFIG.allocator_lookback == 20
    assert CONFIG.qtable_dir == "data"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_config_phase3.py -v`
Expected: FAIL (`AttributeError: ... 'agent_mode'`)

- [ ] **Step 3: Implement** — add these fields to the `Config` dataclass in `algotrading/config.py` (after `enabled_skills`, before `CONFIG = Config()`):

```python
    agent_mode: str = "heuristic"           # "heuristic" | "llm"
    llm_model: str = "claude-opus-4-8"
    rl_alpha: float = 0.1
    rl_gamma: float = 0.95
    rl_epsilon: float = 0.1
    rl_fast: int = 9
    rl_slow: int = 21
    committee_threshold: float = 0.2
    risk_vol_window: int = 20
    risk_vol_threshold: float = 0.04
    allocator_enabled: bool = False
    allocator_lookback: int = 20
    qtable_dir: str = "data"
```

- [ ] **Step 4: Run test to verify it passes** — `./.venv/bin/python -m pytest tests/test_config_phase3.py -v` → PASS

- [ ] **Step 5: Run full suite + commit**

Run: `./.venv/bin/python -m pytest -q` → all pass.
```bash
git add algotrading/config.py tests/test_config_phase3.py
git commit -m "feat: Phase 3 config fields (RL, agents, allocator)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Reinforcement-learning skill (tabular Q-learning)

**Files:**
- Create: `algotrading/skills/rl_qlearn.py`
- Modify: `algotrading/engine.py` (register `rl_qlearn` in `_SKILL_FACTORY`)
- Test: `tests/test_rl_qlearn.py`

**Interfaces:**
- Consumes: `Strategy`, `ema`/`rsi`, `Candle`/`Position`/`Signal`.
- Produces: `discretize(closes, position, fast, slow) -> tuple[int,int,int]`; `RLQLearn(alpha=0.1, gamma=0.95, epsilon=0.0, fast=9, slow=21, qtable_path=None, rng=None)` with `name="rl_qlearn"`, methods `on_candle`, `save(path=None)`, `load(path)`, and module constant `ACTIONS = ("HOLD","ENTER_LONG","CLOSE")`. Registered in `engine._SKILL_FACTORY` under `"rl_qlearn"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rl_qlearn.py
from algotrading.skills.rl_qlearn import RLQLearn, discretize, ACTIONS
from algotrading.models import Candle, Position


def candles_from(prices):
    return [Candle(open=p, high=p + 1, low=p - 1, close=p, volume=1.0, time=1000 + i * 60000)
            for i, p in enumerate(prices)]


def test_discretize_flat_warming_up():
    st = discretize([1.0, 2.0], None, 9, 21)
    assert st == (-1, 0, 0)  # rsi None -> -1, trend 0 (emas None), flat


def test_discretize_long_position():
    closes = [float(x) for x in range(1, 60)]
    pos = Position(strategy="rl_qlearn", side="LONG", entry_price=1, size=1,
                   leverage=3, margin=1, stop_price=1, opened_at=0)
    st = discretize(closes, pos, 9, 21)
    assert st[2] == 1  # position_state long


def test_learn_increases_q_on_positive_reward():
    rl = RLQLearn()
    s, a, ns = (2, 1, 0), "ENTER_LONG", (3, 1, 1)
    before = rl._qrow(s)[a]
    rl._learn(s, a, reward=1.0, next_state=ns)
    assert rl._qrow(s)[a] > before


def test_greedy_picks_highest_q():
    rl = RLQLearn(epsilon=0.0)
    rl._qrow((1, 1, 0))["ENTER_LONG"] = 5.0
    assert rl._best_action((1, 1, 0)) == "ENTER_LONG"


def test_on_candle_returns_signal_and_maps_action():
    rl = RLQLearn(epsilon=0.0)
    # Force a state's best action to ENTER_LONG, then feed a candle in that state while flat
    closes = [float(x) for x in range(1, 60)]
    st = discretize(closes, None, 9, 21)
    rl._qrow(st)["ENTER_LONG"] = 10.0
    sig = rl.on_candle(candles_from(closes), None)
    assert sig.action in ("LONG", "HOLD")  # ENTER_LONG while flat -> LONG
    assert sig.action == "LONG"
    assert "state" in sig.indicators


def test_persistence_roundtrip(tmp_path):
    p = str(tmp_path / "q.json")
    rl = RLQLearn(qtable_path=p)
    rl._qrow((1, 1, 0))["HOLD"] = 3.14
    rl.save()
    rl2 = RLQLearn(qtable_path=p)
    assert rl2._qrow((1, 1, 0))["HOLD"] == 3.14
```

- [ ] **Step 2: Run test to verify it fails** — `./.venv/bin/python -m pytest tests/test_rl_qlearn.py -v` → FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement**

```python
# algotrading/skills/rl_qlearn.py
import json
import os
from algotrading.skills.base import Strategy
from algotrading.skills.indicators import ema, rsi
from algotrading.models import Candle, Position, Signal

ACTIONS = ("HOLD", "ENTER_LONG", "CLOSE")


def discretize(closes, position, fast, slow):
    r = rsi(closes, 14)
    if r is None:
        rsi_bucket = -1
    elif r < 30:
        rsi_bucket = 0
    elif r < 45:
        rsi_bucket = 1
    elif r < 55:
        rsi_bucket = 2
    elif r < 70:
        rsi_bucket = 3
    else:
        rsi_bucket = 4
    f = ema(closes, fast)
    s = ema(closes, slow)
    if f is None or s is None or f == s:
        trend = 0
    else:
        trend = 1 if f > s else -1
    pos_state = 1 if (position is not None and position.side == "LONG") else 0
    return (rsi_bucket, trend, pos_state)


class RLQLearn(Strategy):
    name = "rl_qlearn"

    def __init__(self, alpha=0.1, gamma=0.95, epsilon=0.0, fast=9, slow=21,
                 qtable_path=None, rng=None):
        self.alpha = alpha
        self.gamma = gamma
        self.epsilon = epsilon
        self.fast = fast
        self.slow = slow
        self.qtable_path = qtable_path
        self.q = {}
        self._rng = rng
        self._last_state = None
        self._last_action = None
        self._prev_close = None
        self._prev_long = 0
        if qtable_path and os.path.exists(qtable_path):
            self.load(qtable_path)

    @staticmethod
    def _key(state):
        return f"{state[0]},{state[1]},{state[2]}"

    def _qrow(self, state):
        key = self._key(state)
        if key not in self.q:
            self.q[key] = {a: 0.0 for a in ACTIONS}
        return self.q[key]

    def _best_action(self, state):
        row = self._qrow(state)
        return max(ACTIONS, key=lambda a: row[a])  # first max wins on tie (stable)

    def _learn(self, prev_state, action, reward, next_state):
        row = self._qrow(prev_state)
        best_next = max(self._qrow(next_state).values())
        row[action] += self.alpha * (reward + self.gamma * best_next - row[action])

    def _select(self, state):
        if self.epsilon > 0 and self._rng is not None and self._rng() < self.epsilon:
            idx = int(self._rng() * len(ACTIONS)) % len(ACTIONS)
            return ACTIONS[idx]
        return self._best_action(state)

    def on_candle(self, candles, position):
        closes = [c.close for c in candles]
        cur_close = closes[-1]
        state = discretize(closes, position, self.fast, self.slow)
        if self._last_state is not None and self._prev_close:
            step_ret = (cur_close - self._prev_close) / self._prev_close
            reward = self._prev_long * step_ret
            self._learn(self._last_state, self._last_action, reward, state)
        action = self._select(state)
        is_long = position is not None and position.side == "LONG"
        if action == "ENTER_LONG" and not is_long:
            sig_action, intended_long = "LONG", True
        elif action == "CLOSE" and is_long:
            sig_action, intended_long = "CLOSE", False
        else:
            sig_action, intended_long = "HOLD", is_long
        self._last_state = state
        self._last_action = action
        self._prev_close = cur_close
        self._prev_long = 1 if intended_long else 0
        return Signal(action=sig_action, confidence=0.5,
                      reason=f"RL {action} @ {self._key(state)}",
                      indicators={"state": self._key(state), "action": action})

    def save(self, path=None):
        path = path or self.qtable_path
        if not path:
            return
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.q, f)

    def load(self, path):
        with open(path, "r", encoding="utf-8") as f:
            self.q = json.load(f)
```

Then register it in `algotrading/engine.py`: add the import `from algotrading.skills.rl_qlearn import RLQLearn` near the other skill imports, and add `"rl_qlearn": RLQLearn,` to the `_SKILL_FACTORY` dict.

- [ ] **Step 4: Run test to verify it passes** — `./.venv/bin/python -m pytest tests/test_rl_qlearn.py -v` → PASS (6 passed)

- [ ] **Step 5: Full suite + commit**

Run: `./.venv/bin/python -m pytest -q` → all pass.
```bash
git add algotrading/skills/rl_qlearn.py algotrading/engine.py tests/test_rl_qlearn.py
git commit -m "feat: tabular Q-learning RL strategy

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Agent base + heuristic agents

**Files:**
- Create: `algotrading/agents/__init__.py`
- Create: `algotrading/agents/base.py`
- Create: `algotrading/agents/heuristic.py`
- Test: `tests/test_agents_heuristic.py`

**Interfaces:**
- Produces: `AgentView(stance, confidence, reason)` dataclass; `Agent` ABC with `assess(candles, position) -> AgentView`; `BullAgent(fast=9, slow=21)`, `BearAgent(fast=9, slow=21)`, `RiskSupervisor(window=20, vol_threshold=0.04)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agents_heuristic.py
from algotrading.agents.base import Agent, AgentView
from algotrading.agents.heuristic import BullAgent, BearAgent, RiskSupervisor
from algotrading.models import Candle


def candles_from(prices, span=1.0):
    return [Candle(open=p, high=p + span, low=p - span, close=p, volume=1.0, time=1000 + i * 60000)
            for i, p in enumerate(prices)]


def test_agentview_fields():
    v = AgentView("BULLISH", 0.7, "x")
    assert v.stance == "BULLISH" and v.confidence == 0.7


def test_bull_bullish_on_uptrend():
    v = BullAgent().assess(candles_from([float(x) for x in range(1, 60)]), None)
    assert v.stance == "BULLISH"


def test_bear_bearish_on_downtrend():
    v = BearAgent().assess(candles_from([float(x) for x in range(60, 1, -1)]), None)
    assert v.stance == "BEARISH"


def test_bull_warming_up_neutral():
    assert BullAgent().assess(candles_from([1.0, 2.0]), None).stance == "NEUTRAL"


def test_risk_veto_on_high_volatility():
    # flat price but huge candle ranges -> high volatility -> veto
    prices = [100.0] * 30
    candles = candles_from(prices, span=20.0)  # range ~40 vs ref 100 -> 0.4 > 0.04
    assert RiskSupervisor(window=20, vol_threshold=0.04).assess(candles, None).stance == "BEARISH"


def test_risk_neutral_on_calm():
    prices = [100.0] * 30
    candles = candles_from(prices, span=0.1)
    assert RiskSupervisor(window=20, vol_threshold=0.04).assess(candles, None).stance == "NEUTRAL"
```

- [ ] **Step 2: Run test to verify it fails** — FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement**

```python
# algotrading/agents/__init__.py
```

```python
# algotrading/agents/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from algotrading.models import Candle, Position


@dataclass
class AgentView:
    stance: str        # "BULLISH" | "BEARISH" | "NEUTRAL"
    confidence: float
    reason: str


class Agent(ABC):
    name: str = "agent"

    @abstractmethod
    def assess(self, candles: list[Candle], position: Position | None) -> AgentView:
        raise NotImplementedError
```

```python
# algotrading/agents/heuristic.py
from algotrading.agents.base import Agent, AgentView
from algotrading.skills.indicators import ema, rsi


class BullAgent(Agent):
    name = "bull"

    def __init__(self, fast: int = 9, slow: int = 21):
        self.fast = fast
        self.slow = slow

    def assess(self, candles, position):
        closes = [c.close for c in candles]
        f, s, r = ema(closes, self.fast), ema(closes, self.slow), rsi(closes, 14)
        if f is None or s is None or r is None:
            return AgentView("NEUTRAL", 0.0, "warming up")
        if f > s and r < 70:
            conf = max(0.3, min(1.0, (f - s) / s * 50))
            return AgentView("BULLISH", conf, f"uptrend EMA{self.fast}>EMA{self.slow}, RSI {r:.0f}")
        return AgentView("NEUTRAL", 0.1, "no bullish edge")


class BearAgent(Agent):
    name = "bear"

    def __init__(self, fast: int = 9, slow: int = 21):
        self.fast = fast
        self.slow = slow

    def assess(self, candles, position):
        closes = [c.close for c in candles]
        f, s, r = ema(closes, self.fast), ema(closes, self.slow), rsi(closes, 14)
        if f is None or s is None or r is None:
            return AgentView("NEUTRAL", 0.0, "warming up")
        if f < s and r > 30:
            conf = max(0.3, min(1.0, (s - f) / s * 50))
            return AgentView("BEARISH", conf, f"downtrend EMA{self.fast}<EMA{self.slow}, RSI {r:.0f}")
        return AgentView("NEUTRAL", 0.1, "no bearish edge")


class RiskSupervisor(Agent):
    name = "risk"

    def __init__(self, window: int = 20, vol_threshold: float = 0.04):
        self.window = window
        self.vol_threshold = vol_threshold

    def assess(self, candles, position):
        if len(candles) < self.window:
            return AgentView("NEUTRAL", 0.0, "warming up")
        window = candles[-self.window:]
        ref = sum(c.close for c in window) / len(window)
        vol = (max(c.high for c in window) - min(c.low for c in window)) / ref if ref else 0.0
        if vol > self.vol_threshold:
            conf = min(1.0, vol / self.vol_threshold - 1 + 0.5)
            return AgentView("BEARISH", conf, f"high volatility {vol:.2%} > {self.vol_threshold:.2%} — veto")
        return AgentView("NEUTRAL", 0.2, f"volatility {vol:.2%} acceptable")
```

- [ ] **Step 4: Run test to verify it passes** — PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add algotrading/agents/__init__.py algotrading/agents/base.py algotrading/agents/heuristic.py tests/test_agents_heuristic.py
git commit -m "feat: agent framework + Bull/Bear/RiskSupervisor heuristic agents

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Optional Claude-backed LLM agents

**Files:**
- Create: `algotrading/agents/llm.py`
- Test: `tests/test_agents_llm.py`

**Interfaces:**
- Consumes: `Agent`/`AgentView`, `ema`/`rsi`.
- Produces: `LlmAgent(role, model="claude-opus-4-8", client=None, max_tokens=400)` with `name=f"llm_{role}"`. `assess` lazily imports `anthropic` (unless an explicit `client` is injected), calls `client.messages.create(...)` with a `json_schema` `output_config`, parses `{stance, confidence, reason}`. ANY exception → `AgentView("NEUTRAL", 0.0, "llm unavailable: …")`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agents_llm.py
import json
from algotrading.agents.llm import LlmAgent
from algotrading.agents.base import AgentView
from algotrading.models import Candle


def candles_from(prices):
    return [Candle(open=p, high=p + 1, low=p - 1, close=p, volume=1.0, time=1000 + i * 60000)
            for i, p in enumerate(prices)]


class _Block:
    type = "text"
    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class _FakeMessages:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []
    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp(json.dumps(self._payload))


class _FakeClient:
    def __init__(self, payload):
        self.messages = _FakeMessages(payload)


def test_llm_parses_injected_client():
    client = _FakeClient({"stance": "bullish", "confidence": 0.8, "reason": "trend up"})
    v = LlmAgent("bull", client=client).assess(candles_from([float(x) for x in range(1, 40)]), None)
    assert isinstance(v, AgentView)
    assert v.stance == "BULLISH"  # upper-cased
    assert v.confidence == 0.8
    assert client.messages.calls and client.messages.calls[0]["model"] == "claude-opus-4-8"


def test_llm_no_client_no_anthropic_returns_neutral():
    # anthropic is not installed in the test venv -> lazy import raises -> NEUTRAL, no crash
    v = LlmAgent("risk").assess(candles_from([float(x) for x in range(1, 40)]), None)
    assert v.stance == "NEUTRAL"
    assert v.confidence == 0.0
    assert "llm unavailable" in v.reason


def test_llm_bad_json_returns_neutral():
    class BadMessages:
        def create(self, **kwargs):
            return _Resp("not json")
    class BadClient:
        messages = BadMessages()
    v = LlmAgent("bear", client=BadClient()).assess(candles_from([1.0, 2.0, 3.0]), None)
    assert v.stance == "NEUTRAL"
```

- [ ] **Step 2: Run test to verify it fails** — FAIL (ModuleNotFoundError on `algotrading.agents.llm`)

- [ ] **Step 3: Implement**

```python
# algotrading/agents/llm.py
import json
from algotrading.agents.base import Agent, AgentView
from algotrading.skills.indicators import ema, rsi

_ROLE_PROMPT = {
    "bull": ("You are a BULLISH crypto futures analyst. Make the strongest honest case for "
             "going long BTC/INR given the data. Respond ONLY with the JSON schema."),
    "bear": ("You are a BEARISH crypto futures analyst. Make the strongest honest case for "
             "caution or going short BTC/INR given the data. Respond ONLY with the JSON schema."),
    "risk": ("You are a RISK SUPERVISOR for an automated paper-trading bot. Your job is to VETO "
             "trades when conditions are too risky (high volatility, unclear trend). Be BEARISH "
             "(a veto) only when risk is genuinely elevated. Respond ONLY with the JSON schema."),
}

_SCHEMA = {
    "type": "object",
    "properties": {
        "stance": {"type": "string", "enum": ["BULLISH", "BEARISH", "NEUTRAL"]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["stance", "confidence", "reason"],
    "additionalProperties": False,
}


class LlmAgent(Agent):
    """Optional Claude-backed agent. OFF by default — needs ANTHROPIC_API_KEY and costs money
    per call. Any failure degrades gracefully to a NEUTRAL view."""

    def __init__(self, role: str, model: str = "claude-opus-4-8", client=None, max_tokens: int = 400):
        self.role = role
        self.name = f"llm_{role}"
        self.model = model
        self._client = client
        self.max_tokens = max_tokens

    def _get_client(self):
        if self._client is not None:
            return self._client
        import anthropic  # lazy — only needed in live LLM mode
        self._client = anthropic.Anthropic()
        return self._client

    def _build_prompt(self, candles, position):
        closes = [c.close for c in candles]
        last = closes[-1] if closes else 0.0
        f, s, r = ema(closes, 9), ema(closes, 21), rsi(closes, 14)
        pos = "long" if (position is not None and position.side == "LONG") else "flat"
        recent = ", ".join(f"{c:.0f}" for c in closes[-10:])
        return (f"BTC/INR. Last close {last:.0f}. EMA9={f}, EMA21={s}, RSI14={r}. "
                f"Current position: {pos}. Recent closes: {recent}. Give your stance.")

    def assess(self, candles, position):
        try:
            client = self._get_client()
            resp = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=_ROLE_PROMPT.get(self.role, _ROLE_PROMPT["risk"]),
                output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
                messages=[{"role": "user", "content": self._build_prompt(candles, position)}],
            )
            text = next(b.text for b in resp.content if getattr(b, "type", None) == "text")
            data = json.loads(text)
            return AgentView(str(data["stance"]).upper(), float(data["confidence"]), str(data["reason"]))
        except Exception as exc:  # missing anthropic, no key, network, bad JSON — all -> neutral
            return AgentView("NEUTRAL", 0.0, f"llm unavailable: {exc}")
```

- [ ] **Step 4: Run test to verify it passes** — PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add algotrading/agents/llm.py tests/test_agents_llm.py
git commit -m "feat: optional Claude-backed agents (lazy anthropic, error-tolerant, off by default)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Committee strategy

**Files:**
- Create: `algotrading/skills/committee.py`
- Modify: `algotrading/engine.py` (register `committee`)
- Test: `tests/test_committee.py`

**Interfaces:**
- Consumes: `Strategy`, the heuristic agents, `Candle`/`Position`/`Signal`.
- Produces: `build_agents(mode, cfg) -> tuple[Agent,Agent,Agent]`; `Committee(agents=None, threshold=0.2)` with `name="committee"`. Registered in `engine._SKILL_FACTORY` under `"committee"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_committee.py
from algotrading.skills.committee import Committee, build_agents
from algotrading.agents.base import Agent, AgentView
from algotrading.agents.heuristic import BullAgent, BearAgent, RiskSupervisor
from algotrading.config import CONFIG
from algotrading.models import Candle, Position


def candles_from(prices):
    return [Candle(open=p, high=p + 1, low=p - 1, close=p, volume=1.0, time=1000 + i * 60000)
            for i, p in enumerate(prices)]


class _Stub(Agent):
    def __init__(self, view):
        self._view = view
    def assess(self, candles, position):
        return self._view


def long_pos():
    return Position(strategy="committee", side="LONG", entry_price=100, size=1,
                    leverage=3, margin=33, stop_price=98, opened_at=0)


def test_build_agents_heuristic_default():
    bull, bear, risk = build_agents("heuristic", CONFIG)
    assert isinstance(bull, BullAgent) and isinstance(bear, BearAgent) and isinstance(risk, RiskSupervisor)


def test_risk_veto_closes_long():
    c = Committee(agents=(_Stub(AgentView("BULLISH", 0.9, "x")),
                          _Stub(AgentView("NEUTRAL", 0.0, "x")),
                          _Stub(AgentView("BEARISH", 0.8, "veto"))))
    assert c.on_candle(candles_from([100.0] * 30), long_pos()).action == "CLOSE"


def test_bull_dominant_opens_long():
    c = Committee(agents=(_Stub(AgentView("BULLISH", 0.9, "x")),
                          _Stub(AgentView("NEUTRAL", 0.0, "x")),
                          _Stub(AgentView("NEUTRAL", 0.0, "ok"))), threshold=0.2)
    assert c.on_candle(candles_from([100.0] * 30), None).action == "LONG"


def test_bear_dominant_closes_long():
    c = Committee(agents=(_Stub(AgentView("NEUTRAL", 0.0, "x")),
                          _Stub(AgentView("BEARISH", 0.9, "x")),
                          _Stub(AgentView("NEUTRAL", 0.0, "ok"))), threshold=0.2)
    assert c.on_candle(candles_from([100.0] * 30), long_pos()).action == "CLOSE"


def test_no_consensus_holds():
    c = Committee(agents=(_Stub(AgentView("NEUTRAL", 0.0, "x")),
                          _Stub(AgentView("NEUTRAL", 0.0, "x")),
                          _Stub(AgentView("NEUTRAL", 0.0, "ok"))))
    assert c.on_candle(candles_from([100.0] * 30), None).action == "HOLD"
```

- [ ] **Step 2: Run test to verify it fails** — FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement**

```python
# algotrading/skills/committee.py
from algotrading.skills.base import Strategy
from algotrading.models import Candle, Position, Signal
from algotrading.agents.heuristic import BullAgent, BearAgent, RiskSupervisor


def build_agents(mode, cfg):
    if mode == "llm":
        from algotrading.agents.llm import LlmAgent
        return (LlmAgent("bull", cfg.llm_model),
                LlmAgent("bear", cfg.llm_model),
                LlmAgent("risk", cfg.llm_model))
    return (BullAgent(cfg.rl_fast, cfg.rl_slow),
            BearAgent(cfg.rl_fast, cfg.rl_slow),
            RiskSupervisor(cfg.risk_vol_window, cfg.risk_vol_threshold))


class Committee(Strategy):
    name = "committee"

    def __init__(self, agents=None, threshold: float = 0.2):
        self.threshold = threshold
        self.bull, self.bear, self.risk = agents if agents else (BullAgent(), BearAgent(), RiskSupervisor())

    def on_candle(self, candles, position):
        bull = self.bull.assess(candles, position)
        bear = self.bear.assess(candles, position)
        risk = self.risk.assess(candles, position)
        is_long = position is not None and position.side == "LONG"
        ind = {"bull": bull.stance, "bear": bear.stance, "risk": risk.stance}
        if risk.stance == "BEARISH":
            action = "CLOSE" if is_long else "HOLD"
            return Signal(action=action, confidence=risk.confidence,
                          reason=f"risk veto: {risk.reason}", indicators=ind)
        net = (bull.confidence if bull.stance == "BULLISH" else 0.0) - \
              (bear.confidence if bear.stance == "BEARISH" else 0.0)
        ind["net"] = round(net, 3)
        if net > self.threshold and not is_long:
            return Signal("LONG", confidence=min(1.0, net), reason=f"bull consensus: {bull.reason}", indicators=ind)
        if net < -self.threshold and is_long:
            return Signal("CLOSE", confidence=min(1.0, -net), reason=f"bear consensus: {bear.reason}", indicators=ind)
        return Signal("HOLD", reason="no consensus", indicators=ind)
```

Register in `algotrading/engine.py`: add `from algotrading.skills.committee import Committee` and `"committee": Committee,` to `_SKILL_FACTORY`.

- [ ] **Step 4: Run test to verify it passes** — PASS (5 passed)

- [ ] **Step 5: Full suite + commit**

Run: `./.venv/bin/python -m pytest -q` → all pass.
```bash
git add algotrading/skills/committee.py algotrading/engine.py tests/test_committee.py
git commit -m "feat: Bull/Bear/Risk-Supervisor committee strategy

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Meta-allocator + uniform recent-pnl accessor

**Files:**
- Create: `algotrading/allocator.py`
- Modify: `algotrading/db.py` (add `recent_close_pnls`)
- Modify: `algotrading/ledger.py` (add `recent_close_pnls`)
- Test: `tests/test_allocator.py`

**Interfaces:**
- Produces: `compute_allocations(perf: dict[str,float], *, floor=0.1, cap=1.0) -> dict[str,float]`; `recent_performance(store, strategy, lookback=20) -> float`.
- Adds `recent_close_pnls(strategy, limit) -> list[float]` to BOTH `Database` and `MemoryLedger` (newest-first), so the allocator works against live SQLite and in-memory backtests identically.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_allocator.py
from algotrading.allocator import compute_allocations, recent_performance
from algotrading.db import Database
from algotrading.ledger import MemoryLedger


def test_compute_allocations_winner_gets_more_all_above_floor():
    w = compute_allocations({"a": 5.0, "b": 1.0, "c": -2.0}, floor=0.1, cap=1.0)
    assert w["a"] > w["b"] > w["c"]
    assert all(v >= 0.1 - 1e-9 for v in w.values())
    assert all(v <= 1.0 + 1e-9 for v in w.values())


def test_compute_allocations_equal_scores_equal_weights():
    w = compute_allocations({"a": 1.0, "b": 1.0}, floor=0.2, cap=1.0)
    assert abs(w["a"] - w["b"]) < 1e-9


def test_compute_allocations_empty():
    assert compute_allocations({}) == {}


def test_recent_performance_db(tmp_path):
    db = Database(str(tmp_path / "a.db"))
    db.ensure_strategy("ma_trend", 5000.0)
    db.record_trade("ma_trend", 1, "LONG", "CLOSE", 110, 1, 0.1, 20.0, 1000)
    db.record_trade("ma_trend", 2, "LONG", "CLOSE", 90, 1, 0.1, -10.0, 2000)
    assert recent_performance(db, "ma_trend", 20) == 5.0  # mean of [20, -10]
    db.close()


def test_recent_performance_ledger():
    led = MemoryLedger("rl_qlearn", 5000.0)
    led.record_trade("rl_qlearn", 1, "LONG", "CLOSE", 110, 1, 0.1, 4.0, 1000)
    led.record_trade("rl_qlearn", 1, "LONG", "OPEN", 100, 1, 0.1, -0.1, 900)  # ignored (not CLOSE)
    assert recent_performance(led, "rl_qlearn", 20) == 4.0
```

- [ ] **Step 2: Run test to verify it fails** — FAIL (ModuleNotFoundError on `algotrading.allocator`)

- [ ] **Step 3: Implement**

```python
# algotrading/allocator.py
import math


def compute_allocations(perf, *, floor=0.1, cap=1.0):
    names = list(perf.keys())
    if not names:
        return {}
    scores = [perf[n] for n in names]
    m = max(scores)
    exps = [math.exp(s - m) for s in scores]
    total = sum(exps)
    return {n: floor + (cap - floor) * (e / total) for n, e in zip(names, exps)}


def recent_performance(store, strategy, lookback=20):
    pnls = store.recent_close_pnls(strategy, lookback)
    return sum(pnls) / len(pnls) if pnls else 0.0
```

Add to `algotrading/db.py` `Database`:

```python
    def recent_close_pnls(self, strategy, limit):
        rows = self.conn.execute(
            "SELECT pnl FROM trades WHERE strategy=? AND action='CLOSE' ORDER BY id DESC LIMIT ?",
            (strategy, limit)).fetchall()
        return [float(r["pnl"]) for r in rows]
```

Add to `algotrading/ledger.py` `MemoryLedger`:

```python
    def recent_close_pnls(self, strategy, limit):
        closes = [t["pnl"] for t in self.trades
                  if t["strategy"] == strategy and t["action"] == "CLOSE"]
        return list(reversed(closes))[:limit]
```

- [ ] **Step 4: Run test to verify it passes** — PASS (5 passed)

- [ ] **Step 5: Full suite + commit**

Run: `./.venv/bin/python -m pytest -q` → all pass.
```bash
git add algotrading/allocator.py algotrading/db.py algotrading/ledger.py tests/test_allocator.py
git commit -m "feat: meta-allocator + uniform recent_close_pnls accessor

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Allocator sizing hook in the engine (config-gated, default off)

**Files:**
- Modify: `algotrading/engine.py` (`process_tick` + `_open_position` accept an allocation weight)
- Test: `tests/test_engine_allocator.py`

**Interfaces:**
- Consumes: `allocator.compute_allocations`/`recent_performance`.
- Produces: when `cfg.allocator_enabled` is True, `process_tick` computes a per-strategy allocation weight (from each skill's recent performance via `store.recent_close_pnls`) and `_open_position` sizes with `risk_pct * weight`. When False (default), behaviour is identical to before (weight 1.0).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_engine_allocator.py
from algotrading.engine import process_tick, build_skills
from algotrading.broker import Broker
from algotrading.ledger import MemoryLedger
from algotrading.config import Config
from algotrading.models import Candle


def candles(prices):
    return [Candle(open=p, high=p + 1, low=p - 1, close=p, volume=1.0, time=1000 + i * 60000)
            for i, p in enumerate(prices)]


def _force_long_window():
    # rising then a tick that triggers ma_trend LONG is hard to guarantee; instead use a
    # MemoryLedger and a skill whose signal we control via a stub.
    from algotrading.skills.base import Strategy
    from algotrading.models import Signal

    class AlwaysLong(Strategy):
        name = "ma_trend"
        def on_candle(self, cs, pos):
            return Signal("LONG") if pos is None else Signal("HOLD")
    return AlwaysLong()


def test_allocator_disabled_uses_full_risk():
    cfg = Config(allocator_enabled=False)
    led = MemoryLedger("ma_trend", 5000.0)
    broker = Broker(cfg.fee, cfg.slippage)
    skill = _force_long_window()
    process_tick(led, broker, [skill], candles([100.0] * 30), cfg)
    pos = led.get_open_position("ma_trend")
    assert pos is not None  # opened; with full risk_pct sizing
    size_full = pos.size
    # same again with allocator enabled but no trade history -> perf 0 -> equal weights -> smaller risk
    cfg2 = Config(allocator_enabled=True)
    led2 = MemoryLedger("ma_trend", 5000.0)
    process_tick(led2, broker, [_force_long_window()], candles([100.0] * 30), cfg2)
    pos2 = led2.get_open_position("ma_trend")
    assert pos2 is not None
    # with a single strategy and floor<1, the allocator weight < 1 -> strictly smaller size
    assert pos2.size < size_full
```

- [ ] **Step 2: Run test to verify it fails** — FAIL (allocator not wired; both sizes equal)

- [ ] **Step 3: Implement** — in `algotrading/engine.py`:

Add import near the top: `from algotrading.allocator import compute_allocations, recent_performance`.

Change `_open_position` to accept a `weight` and scale `risk_pct`:

```python
def _open_position(db, broker, skill, side, candle, cfg, now_ms, weight=1.0):
    entry_fill = broker.fill_price(candle.close, side, is_entry=True)
    size, margin = broker.position_size(
        db.get_balance(skill.name), entry_fill, cfg.risk_pct * weight, cfg.stop_pct, cfg.leverage)
    ...
```
(keep the rest of `_open_position` identical; only the `cfg.risk_pct` argument becomes `cfg.risk_pct * weight`.)

In `process_tick`, compute weights once at the top and pass the per-skill weight into `_open_position`:

```python
def process_tick(db, broker, skills, candles, cfg):
    candle = candles[-1]
    now_ms = int(time.time() * 1000)
    if getattr(cfg, "allocator_enabled", False):
        perf = {s.name: recent_performance(db, s.name, cfg.allocator_lookback) for s in skills}
        weights = compute_allocations(perf)
    else:
        weights = {}
    for skill in skills:
        weight = weights.get(skill.name, 1.0)
        position = db.get_open_position(skill.name)
        # ... existing risk checks, signal, log ...
        if signal.action in ("LONG", "SHORT") and position is None:
            _open_position(db, broker, skill, signal.action, candle, cfg, now_ms, weight)
        elif signal.action == "CLOSE" and position is not None:
            _close_position(db, broker, skill, position, candle.close, candle, now_ms)
        # ... existing equity snapshot ...
```
(Only two edits: compute `weights` at the top, and pass `weight` to `_open_position`. Everything else in `process_tick` is unchanged.)

- [ ] **Step 4: Run test to verify it passes** — `./.venv/bin/python -m pytest tests/test_engine_allocator.py -v` → PASS

- [ ] **Step 5: Full suite + commit**

Run: `./.venv/bin/python -m pytest -q` → all pass (existing engine/backtest tests stay green because allocator is default-off and `weight` defaults to 1.0).
```bash
git add algotrading/engine.py tests/test_engine_allocator.py
git commit -m "feat: config-gated allocator sizing hook in process_tick (default off)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:** RL skill (§3)→T2; agent framework+heuristic (§4.1-4.2)→T3; optional LLM agents (§4.3)→T4; committee (§4.4)→T5; allocator (§5)→T6; engine hook (§5)→T7; config (§6)→T1; testing (§8) covered per task. ✅

**Placeholder scan:** Task 7 shows the two precise edits against the existing `process_tick`/`_open_position` (rather than repeating the whole function) — the implementer has the current file; edits are unambiguous (compute `weights`, pass `weight`). All other steps contain full code. ✅

**Type consistency:** `AgentView(stance,confidence,reason)` used identically across agents, llm, committee. `recent_close_pnls(strategy, limit)` added to both `Database` and `MemoryLedger` and consumed by `recent_performance`. `Committee`/`RLQLearn` registered in `_SKILL_FACTORY` with no-arg constructors (defaults), matching `build_skills`. `compute_allocations` returns `{name: weight}` consumed in T7. ✅
