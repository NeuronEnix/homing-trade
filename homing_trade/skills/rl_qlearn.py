import json
import os
from homing_trade.skills.base import Strategy
from homing_trade.skills.indicators import ema, rsi
from homing_trade.models import Signal

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
        if self._last_state is not None and self._prev_close is not None:
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
