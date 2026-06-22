"""CoinDCX public futures microstructure — the ACTUAL traded instrument (Phase 6 #3).

This is the execution source of truth: the same B-<PAIR> the bot trades, on the same venue. Pulls
the live order book (best bid/ask, mid, spread in bps, top-of-book depth imbalance) and, best-effort,
the mark price + funding rate. Public endpoints, no API key (distinct from the trading credentials).

Each piece is independently best-effort: a missing/odd payload for one degrades that field to None
rather than dropping the whole reading. `get_coindcx` is the cache-aware entry the engine injects:
serve-fresh / refetch-stale / fall-back-to-stale-or-None — never crashes the loop.
"""
import time

import requests

SOURCE = "coindcx"
ORDERBOOK_URL = "https://public.coindcx.com/market_data/orderbook"
FUTURES_RT_URL = "https://public.coindcx.com/market_data/v3/current_prices/futures/rt"
DEFAULT_MAX_AGE_SEC = 300           # the book moves fast; keep it fresh-ish (5 min)
_DEPTH = 5                          # levels aggregated for the imbalance read


def _http_fetcher(url, params):
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _levels(side, *, reverse):
    """{price_str: qty_str} -> [(price, qty)] sorted by price (bids: high->low, asks: low->high)."""
    out = []
    for p, q in (side or {}).items():
        try:
            out.append((float(p), float(q)))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x[0], reverse=reverse)
    return out


def parse_orderbook(ob):
    """Order-book snapshot -> {best_bid, best_ask, mid, spread_bps, imbalance} or None. `imbalance`
    is bid depth / (bid+ask depth) over the top _DEPTH levels (>0.5 => heavier bids)."""
    try:
        bids = _levels(ob.get("bids"), reverse=True)
        asks = _levels(ob.get("asks"), reverse=False)
        if not bids or not asks:
            return None
        best_bid, best_ask = bids[0][0], asks[0][0]
        mid = (best_bid + best_ask) / 2
        if mid <= 0:
            return None
        bid_qty = sum(q for _, q in bids[:_DEPTH])
        ask_qty = sum(q for _, q in asks[:_DEPTH])
        depth = bid_qty + ask_qty
        return {"best_bid": best_bid, "best_ask": best_ask, "mid": mid,
                "spread_bps": (best_ask - best_bid) / mid * 10000,
                "imbalance": (bid_qty / depth) if depth > 0 else None}
    except Exception:
        return None


def fetch_orderbook(pair, fetcher=None):
    fetcher = fetcher or _http_fetcher
    try:
        return parse_orderbook(fetcher(ORDERBOOK_URL, {"pair": pair}))
    except Exception:
        return None


def fetch_futures_rt(pair, fetcher=None):
    """Best-effort mark price + funding rate for `pair` from the public real-time futures feed ->
    {mark_price, funding_rate} (either may be None) or None if the instrument isn't found. The
    response shape varies, so parsing is defensive: anything unexpected degrades to None."""
    fetcher = fetcher or _http_fetcher
    try:
        data = fetcher(FUTURES_RT_URL, {})
        prices = data.get("prices", data) if isinstance(data, dict) else {}
        row = prices.get(pair)
        if not isinstance(row, dict):
            return None
        def _num(*keys):
            for k in keys:
                v = row.get(k)
                if v is not None:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        pass
            return None
        return {"mark_price": _num("mark_price", "mp"),
                "funding_rate": _num("funding_rate", "fr")}
    except Exception:
        return None


def fetch_coindcx(pair, fetcher=None):
    """Combined microstructure snapshot for the traded instrument -> dict, or None if NEITHER the
    order book nor the futures feed yielded anything useful."""
    ob = fetch_orderbook(pair, fetcher=fetcher)
    rt = fetch_futures_rt(pair, fetcher=fetcher)
    if not ob and not rt:
        return None
    out = {"pair": pair, "ts": 0}
    if ob:
        out.update(ob)
    if rt:
        out.update(rt)
    return out


def get_coindcx(repo, pair, *, fetcher=None, now=None, max_age_sec=DEFAULT_MAX_AGE_SEC):
    """Cache-aware CoinDCX microstructure for `pair`. Returns the reading dict or None. Serves a
    cached value within `max_age_sec`; else refetches + caches; on failure returns the stale cached
    value (or None). Reads/writes signal_cache(source='coindcx', key=pair). Epoch MS."""
    now = int(now if now is not None else time.time() * 1000)
    cached = repo.get_signal(SOURCE, pair) if hasattr(repo, "get_signal") else None
    if cached and (now - cached["fetched_at"]) < max_age_sec * 1000:
        return cached["value"]
    fresh = fetch_coindcx(pair, fetcher=fetcher)
    if fresh is None:
        return cached["value"] if cached else None
    if hasattr(repo, "upsert_signal"):
        repo.upsert_signal(SOURCE, pair, fresh["ts"], fresh, now)
    return fresh
