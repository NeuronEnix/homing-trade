import json
import subprocess
from homing_trade.skills.llm_trader import LlmTrader, resample, _extract_json
from homing_trade.models import Candle, Position


def candles(n=60, start=1000, step=60000, price=64000.0):
    return [Candle(open=price, high=price + 5, low=price - 5, close=price, volume=1,
                   time=start + i * step) for i in range(n)]


def PROV(interval):  # multi-timeframe provider stub — no network in tests
    return candles()


class _Block:
    type = "text"
    def __init__(self, t):
        self.text = t


class _Resp:
    def __init__(self, t):
        self.content = [_Block(t)]


class _Msgs:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
    def create(self, **kw):
        self.calls += 1
        return _Resp(json.dumps(self.payload))


class _Client:
    def __init__(self, payload):
        self.messages = _Msgs(payload)


def test_long_when_flat():
    c = _Client({"action": "long", "confidence": 0.8, "reason": "aligned uptrend"})
    t = LlmTrader(client=c, interval_sec=900, provider=PROV)
    sig = t.on_candle(candles(), None)
    assert sig.action == "LONG" and sig.confidence == 0.8
    assert c.messages.calls == 1
    assert t._last_decision_ts is not None


def test_cadence_holds_without_new_call():
    c = _Client({"action": "long", "confidence": 0.8, "reason": "x"})
    clock = [1000.0]  # wall-clock seconds, controllable
    t = LlmTrader(client=c, interval_sec=120, provider=PROV, clock=lambda: clock[0])
    t.on_candle(candles(), None)            # first consult
    calls = c.messages.calls
    clock[0] += 60                          # +60s, within the 120s gate
    assert t.on_candle(candles(), None).action == "HOLD"
    assert c.messages.calls == calls        # Claude not consulted again
    clock[0] += 120                         # advance past 120s -> consults again
    t.on_candle(candles(), None)
    assert c.messages.calls == calls + 1


def test_no_client_holds_and_sets_error():
    sig = LlmTrader(provider=PROV).on_candle(candles(), None)  # no client/anthropic -> HOLD + error
    assert sig.action == "HOLD" and "llm unavailable" in sig.reason
    assert sig.error is not None   # surfaced so the engine can alert Discord


def test_captures_observation_prediction_rationale():
    c = _Client({"observation": "15m up, 1m up", "prediction": "continues up",
                 "rationale": "trend aligned", "action": "long", "confidence": 0.7})
    sig = LlmTrader(client=c, provider=PROV).on_candle(candles(), None)
    assert sig.action == "LONG"
    assert sig.meta["observation"] == "15m up, 1m up"
    assert sig.meta["prediction"] == "continues up"
    assert sig.meta["rationale"] == "trend aligned"
    assert sig.raw is not None        # full response persisted


def test_close_downgraded_to_hold_when_flat():
    c = _Client({"action": "close", "confidence": 0.5, "reason": "x"})
    assert LlmTrader(client=c, provider=PROV).on_candle(candles(), None).action == "HOLD"


def test_long_downgraded_to_hold_when_in_position():
    pos = Position(strategy="llm_trader", side="LONG", entry_price=64000, size=1,
                   leverage=15, margin=1, stop_price=63000, opened_at=0)
    c = _Client({"action": "long", "confidence": 0.5, "reason": "x"})
    assert LlmTrader(client=c, provider=PROV).on_candle(candles(), pos).action == "HOLD"


def test_ai_can_shorten_next_poll():
    # configured max = 300s, but the AI asks to re-check in 120s
    c = _Client({"observation": "o", "prediction": "p", "rationale": "r",
                 "action": "hold", "confidence": 0.3, "next_check_in_sec": 120})
    clock = [1000.0]
    t = LlmTrader(client=c, interval_sec=300, provider=PROV, clock=lambda: clock[0])
    t.on_candle(candles(), None)
    assert t._next_interval_sec == 120        # honored the AI's shorter request
    clock[0] += 90                            # +90s, still inside the 120s gate
    assert t.on_candle(candles(), None).action == "HOLD"
    assert c.messages.calls == 1
    clock[0] += 60                            # now +150s > 120s -> re-consults
    t.on_candle(candles(), None)
    assert c.messages.calls == 2


def test_next_poll_clamped_to_max_and_floor():
    over = _Client({"observation": "o", "prediction": "p", "rationale": "r",
                    "action": "hold", "confidence": 0.1, "next_check_in_sec": 9999})
    t = LlmTrader(client=over, interval_sec=300, provider=PROV)
    t.on_candle(candles(), None)
    assert t._next_interval_sec == 300        # capped at the configured max
    under = _Client({"observation": "o", "prediction": "p", "rationale": "r",
                     "action": "hold", "confidence": 0.1, "next_check_in_sec": 0})
    t2 = LlmTrader(client=under, interval_sec=300, provider=PROV, min_interval_sec=30)
    t2.on_candle(candles(), None)
    assert t2._next_interval_sec == 30        # floored


def test_resample_15m():
    cs = candles(n=30)
    r = resample(cs, 15)
    assert len(r) == 2
    assert r[0].open == cs[0].open and r[0].close == cs[14].close


# --- CLI backend (claude headless, no API key) ---
class _Proc:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def _cli_envelope(result_text, is_error=False):
    return json.dumps({"type": "result", "is_error": is_error, "result": result_text})


def test_cli_backend_parses_decision(monkeypatch):
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _Proc(_cli_envelope('Here you go: {"action":"short","confidence":0.7,"reason":"bearish"}'))

    monkeypatch.setattr(subprocess, "run", fake_run)
    sig = LlmTrader(backend="cli", provider=PROV).on_candle(candles(), None)
    assert sig.action == "SHORT" and sig.confidence == 0.7
    assert captured["cmd"][0] == "claude" and "-p" in captured["cmd"]


def test_cli_backend_error_holds(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _Proc("", returncode=1, stderr="boom"))
    assert LlmTrader(backend="cli", provider=PROV).on_candle(candles(), None).action == "HOLD"


def test_extract_json_from_prose():
    assert _extract_json('blah {"action":"HOLD"} trailing')["action"] == "HOLD"
