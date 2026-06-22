from datetime import datetime, timezone
import requests
from homing_trade.models import Candle

CANDLES_URL = "https://public.coindcx.com/market_data/candles"
TICKER_URL = "https://api.coindcx.com/exchange/ticker"

# Intervals CoinDCX serves, minute -> day/week units.
INTERVALS = ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "1d", "3d", "1w", "1M")


def to_ms(t):
    """ISO-8601 UTC string (e.g. '2026-06-20T00:00:00Z') or epoch ms -> epoch milliseconds.
    None / '' pass through as None."""
    if t is None or t == "":
        return None
    if isinstance(t, (int, float)):
        return int(t)
    dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def parse_candles(raw: list[dict]) -> list[Candle]:
    candles = [
        Candle(open=float(r["open"]), high=float(r["high"]), low=float(r["low"]),
               close=float(r["close"]), volume=float(r["volume"]), time=int(r["time"]))
        for r in raw
    ]
    candles.sort(key=lambda c: c.time)
    return candles


def _http_fetcher(url: str, params: dict) -> list[dict]:
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_candles(pair: str, interval: str, limit: int = 200, *,
                start=None, end=None, fetcher=None) -> list[Candle]:
    """Fetch candles for any supported interval. Optionally bound by a date range —
    `start`/`end` accept ISO-8601 UTC strings or epoch ms."""
    fetcher = fetcher or _http_fetcher
    params = {"pair": pair, "interval": interval, "limit": limit}
    s, e = to_ms(start), to_ms(end)
    if s is not None:
        params["startTime"] = s
    if e is not None:
        params["endTime"] = e
    raw = fetcher(CANDLES_URL, params)
    return parse_candles(raw)


def get_prices(symbols, *, fetcher=None) -> dict:
    """Live last-price + 24h change for the given ticker markets (e.g. 'BTCUSDT').
    Returns {symbol: {'last': float, 'change': float} or None if not found}."""
    fetcher = fetcher or _http_fetcher
    data = fetcher(TICKER_URL, {})
    by_market = {d.get("market"): d for d in data}
    out = {}
    for s in symbols:
        d = by_market.get(s)
        if d:
            try:
                out[s] = {"last": float(d.get("last_price", 0)),
                          "change": float(d.get("change_24_hour", 0) or 0)}
            except (TypeError, ValueError):
                out[s] = None
        else:
            out[s] = None
    return out
