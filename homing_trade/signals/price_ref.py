"""Independent reference price — CoinGecko (Phase 6 #4), to SANITY-CHECK the venue.

CoinGecko's public `simple/price` works keyless (rate-limited); a free Demo key (read from the
gitignored env var named by cfg.coingecko_key_env, sent as `x_cg_demo_api_key`) just raises the
limit. Either way the fetch degrades to None on failure — the signal is contextual, never
load-bearing. One call covers all traded assets; cached in signal_cache(source='price_ref').

The point: a large gap between this independent USD reference and the venue's order-book mid/mark
flags stale/illiquid venue data — a reason for caution, not a trade.
"""
import os

import requests

from homing_trade.signals.cache import cached_signal

SOURCE = "price_ref"
KEY = "usd"
SIMPLE_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
DEFAULT_MAX_AGE_SEC = 600           # reference price; 10-min freshness is ample
COIN_IDS = ("bitcoin", "ethereum")  # the assets the bot trades


def _http_fetcher(url, params):
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def resolve_key(env_var_name):
    """The CoinGecko Demo key from the named env var, or None (keyless public tier still works)."""
    return os.environ.get(env_var_name or "", "") or None


def fetch_price_ref(coin_ids=COIN_IDS, *, fetcher=None, api_key=None):
    """{coin_id: {usd, change_24h, market_cap}} for the given ids, or None on any error / no usable
    row. A Demo `api_key`, when given, is sent as the CoinGecko demo query param."""
    fetcher = fetcher or _http_fetcher
    try:
        params = {"ids": ",".join(coin_ids), "vs_currencies": "usd",
                  "include_24hr_change": "true", "include_market_cap": "true"}
        if api_key:
            params["x_cg_demo_api_key"] = api_key
        data = fetcher(SIMPLE_PRICE_URL, params)
        out = {}
        for cid in coin_ids:
            row = data.get(cid) if isinstance(data, dict) else None
            if isinstance(row, dict) and _f(row.get("usd")) is not None:
                out[cid] = {"usd": _f(row.get("usd")),
                            "change_24h": _f(row.get("usd_24h_change")),
                            "market_cap": _f(row.get("usd_market_cap"))}
        return out or None
    except Exception:
        return None


def get_price_ref(repo, *, fetcher=None, now=None, max_age_sec=DEFAULT_MAX_AGE_SEC, api_key=None):
    """Cache-aware reference price for the traded assets. Returns the {coin_id: {...}} dict or None.
    Serves a cached value within `max_age_sec`; else refetches + caches; on failure returns the stale
    cached value (or None). Reads/writes signal_cache(source='price_ref', key='usd'). Epoch MS."""
    return cached_signal(repo, SOURCE, KEY,
                         lambda: fetch_price_ref(fetcher=fetcher, api_key=api_key),
                         now=now, max_age_sec=max_age_sec)
