from homing_trade.db import Database
from homing_trade.models import Candle


def mk(time, close, src_close=None):
    return Candle(open=close, high=close + 1, low=close - 1, close=close, volume=1.0, time=time)


def db(tmp_path):
    return Database(str(tmp_path / "c.db"))


def test_save_and_range(tmp_path):
    d = db(tmp_path)
    d.save_candles("I-BTC_INR", "1m", [mk(2000, 101), mk(1000, 100), mk(3000, 102)], "history")
    got = d.get_candles_range("I-BTC_INR", "1m", 1000, 3000)
    assert [c.time for c in got] == [1000, 2000, 3000]  # ascending
    assert got[0].close == 100


def test_upsert_dedupe_updates_values(tmp_path):
    d = db(tmp_path)
    d.save_candles("I-BTC_INR", "1m", [mk(1000, 100)], "history")
    d.save_candles("I-BTC_INR", "1m", [mk(1000, 999)], "live")  # same time -> update, no dup
    got = d.get_candles_range("I-BTC_INR", "1m", 0, 5000)
    assert len(got) == 1
    assert got[0].close == 999
    assert d.count_candles("I-BTC_INR", "1m", source="live") == 1
    assert d.count_candles("I-BTC_INR", "1m", source="history") == 0


def test_range_source_filter(tmp_path):
    d = db(tmp_path)
    d.save_candles("I-BTC_INR", "1m", [mk(1000, 100)], "history")
    d.save_candles("I-BTC_INR", "1m", [mk(2000, 101)], "live")
    assert [c.time for c in d.get_candles_range("I-BTC_INR", "1m", 0, 9000, "live")] == [2000]
    assert [c.time for c in d.get_candles_range("I-BTC_INR", "1m", 0, 9000, "history")] == [1000]
    assert len(d.get_candles_range("I-BTC_INR", "1m", 0, 9000, "all")) == 2


def test_bounds_and_count(tmp_path):
    d = db(tmp_path)
    assert d.get_candle_bounds("I-BTC_INR", "1m") is None
    d.save_candles("I-BTC_INR", "1m", [mk(1000, 100), mk(3000, 102)], "history")
    assert d.get_candle_bounds("I-BTC_INR", "1m") == (1000, 3000)
    assert d.count_candles("I-BTC_INR", "1m") == 2
