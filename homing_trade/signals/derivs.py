"""Derivatives microstructure ingestion — perp funding rate + open interest (Phase 6 #2).

Binance USDT-perp public endpoints (no API key). `fetch_derivs(symbol)` snapshots the current
funding rate, mark price, and open interest across whatever venues are wired (Binance today; OKX/
Bybit plug into the `_VENUES` tuple next) and computes a cross-venue `funding_skew`. `get_derivs`
is the cache-aware entry the engine injects into the AI context: serve-fresh / refetch-stale /
fall-back-to-stale-or-None — every fetch degrades to "unavailable", never crashing the loop.

Funding is a positioning gauge: strongly POSITIVE funding => longs crowded/paying (contrarian
caution on fresh longs); strongly NEGATIVE => shorts crowded. It is confluence/contra context,
never a standalone trigger.
"""
import time

import requests

SOURCE = "derivs"
BINANCE_PREMIUM_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
BINANCE_OI_URL = "https://fapi.binance.com/fapi/v1/openInterest"
DEFAULT_MAX_AGE_SEC = 900           # funding/OI move faster than F&G; 15-min freshness


def _http_fetcher(url, params):
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def binance_symbol(pair):
    """CoinDCX futures pair (e.g. 'B-BTC_USDT') -> Binance USDT-perp symbol ('BTCUSDT')."""
    return str(pair).replace("B-", "").replace("_", "").upper()


def fetch_binance(symbol, fetcher=None):
    """Funding rate + mark price + open interest for a Binance USDT-perp symbol -> dict, or None
    if the funding call fails. Open interest is best-effort: if ONLY the OI call fails, funding
    still returns with open_interest=None."""
    fetcher = fetcher or _http_fetcher
    try:
        prem = fetcher(BINANCE_PREMIUM_URL, {"symbol": symbol})
        funding = float(prem["lastFundingRate"])
        mark = float(prem["markPrice"]) if prem.get("markPrice") is not None else None
        ts = int(prem.get("time") or 0)
    except Exception:
        return None
    try:
        oi = float(fetcher(BINANCE_OI_URL, {"symbol": symbol})["openInterest"])
    except Exception:
        oi = None
    return {"venue": "binance", "symbol": symbol, "funding_rate": funding,
            "mark_price": mark, "open_interest": oi, "ts": ts}


# Wired venues, in priority order. OKX/Bybit append here (same reading shape) — fetch_derivs and
# funding_skew already aggregate over whatever responds.
_VENUES = (fetch_binance,)


def funding_skew(by_venue):
    """Cross-venue funding aggregation over a {venue: funding_rate} map ->
    {mean, spread, venues} (spread = max-min, 0 with one venue). None when no numeric rate."""
    rates = {v: r for v, r in (by_venue or {}).items() if isinstance(r, (int, float))}
    if not rates:
        return None
    vals = list(rates.values())
    return {"mean": sum(vals) / len(vals), "spread": max(vals) - min(vals), "venues": rates}


def fetch_derivs(symbol, fetcher=None):
    """Snapshot the symbol's derivatives across all wired venues -> {symbol, venues:[...],
    funding_skew, ts} or None if NO venue responded. Never raises."""
    readings = [r for r in (fn(symbol, fetcher=fetcher) for fn in _VENUES) if r]
    if not readings:
        return None
    skew = funding_skew({r["venue"]: r["funding_rate"] for r in readings})
    return {"symbol": symbol, "venues": readings, "funding_skew": skew, "ts": readings[0]["ts"]}


def get_derivs(repo, symbol, *, fetcher=None, now=None, max_age_sec=DEFAULT_MAX_AGE_SEC):
    """Cache-aware derivatives snapshot for `symbol`. Returns the reading dict or None. Serves a
    cached value within `max_age_sec`; else refetches + caches; on fetch failure returns the stale
    cached value (or None). Reads/writes signal_cache(source='derivs', key=symbol). Epoch MS."""
    now = int(now if now is not None else time.time() * 1000)
    cached = repo.get_signal(SOURCE, symbol) if hasattr(repo, "get_signal") else None
    if cached and (now - cached["fetched_at"]) < max_age_sec * 1000:
        return cached["value"]
    fresh = fetch_derivs(symbol, fetcher=fetcher)
    if fresh is None:
        return cached["value"] if cached else None
    if hasattr(repo, "upsert_signal"):
        repo.upsert_signal(SOURCE, symbol, fresh["ts"], fresh, now)
    return fresh
