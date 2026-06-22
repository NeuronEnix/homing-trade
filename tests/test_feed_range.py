from homing_trade.feed import get_candles, to_ms, INTERVALS


def test_to_ms_iso_utc_and_passthrough():
    assert to_ms("1970-01-01T00:00:00Z") == 0
    assert to_ms("1970-01-01T00:00:01Z") == 1000
    assert to_ms(None) is None
    assert to_ms("") is None
    assert to_ms(1500) == 1500           # epoch ms passes through


def test_intervals_cover_minute_to_day():
    for iv in ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "1d"):
        assert iv in INTERVALS


def test_get_candles_passes_interval_limit_and_range():
    captured = {}
    def fetcher(url, params):
        captured.update(params)
        return [{"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "time": 60000}]
    get_candles("B-BTC_USDT", "1h", limit=300, start="2026-06-20T00:00:00Z",
                end="2026-06-22T00:00:00Z", fetcher=fetcher)
    assert captured["interval"] == "1h" and captured["limit"] == 300
    assert captured["startTime"] == to_ms("2026-06-20T00:00:00Z")
    assert captured["endTime"] == to_ms("2026-06-22T00:00:00Z")


def test_get_candles_no_range_omits_times():
    captured = {}
    get_candles("B-BTC_USDT", "5m", limit=100,
                fetcher=lambda u, p: captured.update(p) or [])
    assert "startTime" not in captured and "endTime" not in captured
