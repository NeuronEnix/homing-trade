import json
from homing_trade.skills.llm_trader import LlmTrader, resample
from homing_trade.models import Candle, Position


def candles(n=60, start=1000, step=60000, price=64000.0):
    return [Candle(open=price, high=price + 5, low=price - 5, close=price, volume=1,
                   time=start + i * step) for i in range(n)]


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
    t = LlmTrader(client=c, interval_min=15)
    sig = t.on_candle(candles(), None)
    assert sig.action == "LONG" and sig.confidence == 0.8
    assert c.messages.calls == 1
    assert t._last_decision_time is not None


def test_cadence_holds_without_new_call():
    c = _Client({"action": "long", "confidence": 0.8, "reason": "x"})
    t = LlmTrader(client=c, interval_min=15)
    cs = candles()
    t.on_candle(cs, None)
    calls = c.messages.calls
    nxt = cs + [Candle(open=64000, high=64005, low=63995, close=64000, volume=1,
                       time=cs[-1].time + 60000)]  # only +1 minute -> within 15m
    sig = t.on_candle(nxt, None)
    assert sig.action == "HOLD"
    assert c.messages.calls == calls  # Claude not consulted again


def test_no_client_holds():
    sig = LlmTrader().on_candle(candles(), None)  # anthropic not installed -> HOLD
    assert sig.action == "HOLD" and "llm unavailable" in sig.reason


def test_close_downgraded_to_hold_when_flat():
    c = _Client({"action": "close", "confidence": 0.5, "reason": "x"})
    assert LlmTrader(client=c).on_candle(candles(), None).action == "HOLD"


def test_long_downgraded_to_hold_when_in_position():
    pos = Position(strategy="llm_trader", side="LONG", entry_price=64000, size=1,
                   leverage=15, margin=1, stop_price=63000, opened_at=0)
    c = _Client({"action": "long", "confidence": 0.5, "reason": "x"})
    assert LlmTrader(client=c).on_candle(candles(), pos).action == "HOLD"


def test_resample_15m():
    cs = candles(n=30)
    r = resample(cs, 15)
    assert len(r) == 2
    assert r[0].open == cs[0].open and r[0].close == cs[14].close
