import json
from pathlib import Path
from homing_trade.feed import parse_candles, get_candles, CANDLES_URL

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "candles_sample.json").read_text())


def test_parse_sorts_ascending_by_time():
    candles = parse_candles(FIXTURE)
    times = [c.time for c in candles]
    assert times == sorted(times)
    assert candles[0].time == 1717000000000
    assert candles[-1].close == 104.0


def test_get_candles_uses_injected_fetcher():
    captured = {}

    def fake_fetcher(url, params):
        captured["url"] = url
        captured["params"] = params
        return FIXTURE

    candles = get_candles("I-BTC_INR", "1m", limit=3, fetcher=fake_fetcher)
    assert captured["url"] == CANDLES_URL
    assert captured["params"]["pair"] == "I-BTC_INR"
    assert captured["params"]["interval"] == "1m"
    assert captured["params"]["limit"] == 3
    assert len(candles) == 3
    assert candles[0].time < candles[-1].time
