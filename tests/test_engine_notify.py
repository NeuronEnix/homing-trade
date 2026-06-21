import algotrading.engine as eng
from algotrading.engine import run
from algotrading.db import Database
from algotrading.config import Config
from algotrading.skills.base import Strategy
from algotrading.models import Candle, Signal


class _RecNotifier:
    def __init__(self):
        self.events = []
    def notify(self, level, title, message):
        self.events.append((level, title, message))


class AlwaysLong(Strategy):
    name = "ma_trend"
    def on_candle(self, candles, position):
        return Signal("LONG") if position is None else Signal("HOLD")


def candles():
    return [Candle(open=100, high=101, low=99, close=100, volume=1, time=1000 + i * 60000)
            for i in range(30)]


def test_trades_after(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.record_trade("ma_trend", 1, "LONG", "OPEN", 100, 1, 0.1, -0.1, 1000)
    db.record_trade("ma_trend", 1, "LONG", "CLOSE", 110, 1, 0.1, 9.9, 2000)
    after = db.trades_after(1)  # ids start at 1; first row has id 1 -> only the 2nd returned
    assert len(after) == 1 and after[0]["action"] == "CLOSE"
    assert [t["id"] for t in db.trades_after(0)] == [1, 2]  # oldest-first
    db.close()


def test_run_emits_trade_alerts(tmp_path, monkeypatch):
    monkeypatch.setitem(eng._SKILL_FACTORY, "ma_trend", AlwaysLong)
    cfg = Config(db_path=str(tmp_path / "n.db"), enabled_skills=["ma_trend"])
    raw = [{"open": c.open, "high": c.high, "low": c.low, "close": c.close,
            "volume": c.volume, "time": c.time} for c in candles()]
    notifier = _RecNotifier()
    run(cfg, fetcher=lambda url, params: raw, max_ticks=1, sleeper=lambda s: None, notifier=notifier)
    trade_events = [e for e in notifier.events if e[0] == "trade"]
    assert len(trade_events) >= 1
    assert "ma_trend" in trade_events[0][1]
