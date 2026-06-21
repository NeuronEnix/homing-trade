import requests
from homing_trade.models import Candle

CANDLES_URL = "https://public.coindcx.com/market_data/candles"


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
