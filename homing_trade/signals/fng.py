"""Crypto Fear & Greed index ingestion — Alternative.me (free, no API key).

`fetch_fng()` pulls the current reading; `get_fng(repo)` is the cache-aware entry the engine wires
into the AI context: it serves a fresh cached value, refetches when stale, and on ANY fetch failure
falls back to the last cached value (or None) — so the signal degrades to "unavailable" and never
crashes the loop. Readings are cached in SQLite (signal_cache, source='fng') with `fetched_at`,
which both rate-limits the upstream and makes a decision replayable.

The fetcher is injectable so tests run offline and deterministic.
"""
import time

import requests

FNG_URL = "https://api.alternative.me/fng/"
SOURCE = "fng"
KEY = "latest"
DEFAULT_MAX_AGE_SEC = 3600          # F&G updates ~daily; an hour-fresh cache is plenty


def _http_fetcher(url, params):
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def fetch_fng(fetcher=None):
    """Fetch + parse the current reading -> {'value': int, 'classification': str, 'ts': int(ms)}
    or None on any error (network, bad shape, parse) — never raises."""
    fetcher = fetcher or _http_fetcher
    try:
        data = fetcher(FNG_URL, {"limit": 1})
        row = (data.get("data") or [])[0]
        return {"value": int(row["value"]),
                "classification": str(row.get("value_classification", "")),
                "ts": int(row["timestamp"]) * 1000}      # API gives epoch SECONDS -> ms
    except Exception:
        return None


def get_fng(repo, *, fetcher=None, now=None, max_age_sec=DEFAULT_MAX_AGE_SEC):
    """Cache-aware current Fear & Greed. Returns {'value', 'classification', 'ts'} or None.

    Serves a cached value still within `max_age_sec`; otherwise refetches and caches it. If the
    refetch fails, returns the (stale) cached value when present, else None — the loop keeps running.
    `now`/`fetched_at` are epoch MS; reads/writes signal_cache(source='fng', key='latest')."""
    now = int(now if now is not None else time.time() * 1000)
    cached = repo.get_signal(SOURCE, KEY) if hasattr(repo, "get_signal") else None
    if cached and (now - cached["fetched_at"]) < max_age_sec * 1000:
        return cached["value"]
    fresh = fetch_fng(fetcher=fetcher)
    if fresh is None:
        return cached["value"] if cached else None
    if hasattr(repo, "upsert_signal"):
        repo.upsert_signal(SOURCE, KEY, fresh["ts"], fresh, now)
    return fresh
