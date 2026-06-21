import requests
from homing_trade.models import Candle

CANDLES_URL = "https://public.coindcx.com/market_data/candles"
TICKER_URL = "https://api.coindcx.com/exchange/ticker"


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


def get_candles(pair: str, interval: str, limit: int = 200, *, fetcher=None) -> list[Candle]:
    fetcher = fetcher or _http_fetcher
    params = {"pair": pair, "interval": interval, "limit": limit}
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
