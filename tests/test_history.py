from homing_trade.db import Database
from homing_trade.history import ensure_history

STEP = 3_600_000  # 1h in ms
NOW = 1000 * STEP  # interval-aligned "now"


def make_fetcher(calls):
    # returns candles at STEP intervals within [startTime, min(endTime, startTime+STEP*(limit-1))]
    def fetcher(url, params):
        calls.append(params)
        t = params["startTime"]
        end = params["endTime"]
        limit = params["limit"]
        out = []
        while t <= end and len(out) < limit:
            out.append({"open": 100.0, "high": 101.0, "low": 99.0,
                        "close": 100.0, "volume": 1.0, "time": t})
            t += STEP
        return out
    return fetcher


def test_first_call_fetches_and_stores(tmp_path):
    d = Database(str(tmp_path / "h.db"))
    calls = []
    out = ensure_history(d, "I-BTC_INR", "1h", 1, NOW, fetcher=make_fetcher(calls))
    assert len(calls) >= 1
    assert len(out) > 0
    assert d.count_candles("I-BTC_INR", "1h", source="history") == len(out)


def test_second_call_makes_no_fetch(tmp_path):
    d = Database(str(tmp_path / "h.db"))
    ensure_history(d, "I-BTC_INR", "1h", 1, NOW, fetcher=make_fetcher([]))

    def boom(url, params):
        raise AssertionError("should not fetch when data already present")

    out = ensure_history(d, "I-BTC_INR", "1h", 1, NOW, fetcher=boom)  # must not raise
    assert len(out) > 0


def test_gap_fill_only_fetches_missing(tmp_path):
    d = Database(str(tmp_path / "h.db"))
    from homing_trade.models import Candle
    # Pre-store a middle band [NOW-20h .. NOW-10h]
    mid = [Candle(open=100, high=101, low=99, close=100, volume=1, time=NOW - k * STEP)
           for k in range(10, 21)]
    d.save_candles("I-BTC_INR", "1h", mid, "history")
    calls = []
    ensure_history(d, "I-BTC_INR", "1h", 1, NOW, fetcher=make_fetcher(calls))
    # missing older span (< band) and newer span (> band) -> at least 2 fetch calls
    assert len(calls) >= 2


def test_fetch_error_returns_stored_without_raising(tmp_path):
    d = Database(str(tmp_path / "h.db"))

    def boom(url, params):
        raise RuntimeError("network down")

    out = ensure_history(d, "I-BTC_INR", "1h", 1, NOW, fetcher=boom)
    assert out == []  # nothing stored, nothing raised
