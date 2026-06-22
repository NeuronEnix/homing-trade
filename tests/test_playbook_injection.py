"""Phase-4 #6: inject the current (human-approved) playbook into the LLM prompt + bump
prompt_version. The playbook is the channel by which the learn->correct loop actually changes
behavior — but only AFTER a human approves a proposal and it's published. Injection is bounded
(top-K), refines rather than blindly appends (handled upstream by reflection), degrades to the
base prompt on any read failure, and never crashes the consult.
"""
import json
from homing_trade.skills.llm_trader import LlmTrader, PROMPT_VERSION
from homing_trade.models import Candle


def candles(n=60, start=1000, step=60000, price=64000.0):
    return [Candle(open=price, high=price + 5, low=price - 5, close=price, volume=1,
                   time=start + i * step) for i in range(n)]


def PROV(interval, limit=150, start=None, end=None):  # chart provider stub — no network
    return candles()


class _Block:
    type = "text"
    def __init__(self, t):
        self.text = t


class _Resp:
    def __init__(self, t):
        self.content = [_Block(t)]


class _CapMsgs:
    """Records the kwargs of the last create() so tests can inspect what was actually sent."""
    def __init__(self, payload):
        self.payload = payload
        self.kw = None

    def create(self, **kw):
        self.kw = kw
        return _Resp(json.dumps(self.payload))


class _CapClient:
    def __init__(self, payload):
        self.messages = _CapMsgs(payload)

    def user(self):
        return self.messages.kw["messages"][0]["content"]

    def system(self):
        return self.messages.kw["system"]

    def ctx(self):
        """The context dict the model actually received (the JSON after 'Charts:\\n')."""
        return json.loads(self.user().split("Charts:\n", 1)[1])


PAYLOAD = {"action": "HOLD", "confidence": 0.5, "observation": "o", "prediction": "p",
           "rationale": "r", "next_check_in_sec": 120}


def pb(version="ma-v2", rules=("trend only", "skip chop")):
    """A playbook_provider returning a published playbook row (version + rules_json)."""
    return lambda: {"version": version, "rules_json": json.dumps({"rules": list(rules)})}


def trader(client, **kw):
    return LlmTrader(client=client, interval_sec=900, provider=PROV, **kw)


def test_playbook_rules_injected_into_context_and_versioned():
    c = _CapClient(PAYLOAD)
    t = trader(c)
    t.set_playbook_provider(pb(rules=["trend only", "skip chop"]))
    sig = t.on_candle(candles(), None)
    assert sig.error is None
    assert c.ctx()["playbook"] == ["trend only", "skip chop"]   # rules reached the model
    assert "playbook" in c.system().lower()                      # told what they are
    assert sig.meta["playbook_version"] == "ma-v2"               # engine can persist it
    assert sig.meta["prompt_version"] == f"{PROMPT_VERSION}+ma-v2"
    assert sig.meta["prompt_hash"]                               # replayable


def test_no_provider_means_no_playbook_and_base_prompt_version():
    c = _CapClient(PAYLOAD)
    sig = trader(c).on_candle(candles(), None)
    assert "playbook" not in c.ctx()                             # back-compat: nothing injected
    assert sig.meta["playbook_version"] is None
    assert sig.meta["prompt_version"] == PROMPT_VERSION


def test_playbook_is_bounded_top_k():
    c = _CapClient(PAYLOAD)
    t = trader(c, playbook_max_rules=3)
    t.set_playbook_provider(pb(rules=[f"rule{i}" for i in range(50)]))
    t.on_candle(candles(), None)
    assert c.ctx()["playbook"] == ["rule0", "rule1", "rule2"]    # only the top-K


def test_provider_error_degrades_to_base_prompt():
    c = _CapClient(PAYLOAD)
    t = trader(c)
    def boom():
        raise RuntimeError("ledger down")
    t.set_playbook_provider(boom)
    sig = t.on_candle(candles(), None)                           # must NOT crash the consult
    assert "playbook" not in c.ctx()
    assert sig.meta["prompt_version"] == PROMPT_VERSION and sig.meta["playbook_version"] is None


def test_malformed_rules_json_degrades():
    c = _CapClient(PAYLOAD)
    t = trader(c)
    t.set_playbook_provider(lambda: {"version": "v9", "rules_json": "not json at all"})
    sig = t.on_candle(candles(), None)
    assert "playbook" not in c.ctx()
    assert sig.meta["playbook_version"] is None                  # unreadable -> no version claimed


def test_empty_rules_inject_nothing_and_claim_no_version():
    # A published-but-empty playbook isn't a behavior change: the prompt is effectively the base
    # prompt, so prompt_version must stay base and no version is claimed.
    c = _CapClient(PAYLOAD)
    t = trader(c)
    t.set_playbook_provider(lambda: {"version": "v1", "rules_json": json.dumps({"rules": []})})
    sig = t.on_candle(candles(), None)
    assert "playbook" not in c.ctx()
    assert sig.meta["playbook_version"] is None
    assert sig.meta["prompt_version"] == PROMPT_VERSION


def test_non_string_rules_are_filtered_out():
    c = _CapClient(PAYLOAD)
    t = trader(c)
    t.set_playbook_provider(lambda: {"version": "v3",
                                     "rules_json": json.dumps({"rules": ["keep", 5, "", None, "also"]})})
    t.on_candle(candles(), None)
    assert c.ctx()["playbook"] == ["keep", "also"]               # junk dropped, strings kept


def test_scalar_rules_json_is_not_iterated_into_char_rules():
    # A corrupted row whose rules_json decodes to a bare JSON string must NOT be iterated into
    # per-character "rules"; it injects nothing.
    c = _CapClient(PAYLOAD)
    t = trader(c)
    t.set_playbook_provider(lambda: {"version": "v4", "rules_json": json.dumps("hello")})
    sig = t.on_candle(candles(), None)
    assert "playbook" not in c.ctx()
    assert sig.meta["playbook_version"] is None


class _BoomMsgs:
    def create(self, **kw):
        raise RuntimeError("network down")


class _BoomClient:
    messages = _BoomMsgs()


def test_error_path_still_carries_provenance():
    # A failing consult still attaches prompt_version/hash so the error row is attributable.
    t = trader(_BoomClient())
    sig = t.on_candle(candles(), None)
    assert sig.error and sig.action == "HOLD"
    assert sig.meta["prompt_version"] == PROMPT_VERSION and sig.meta["prompt_hash"]
    assert sig.meta["playbook_version"] is None
