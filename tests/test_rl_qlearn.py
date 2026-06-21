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
