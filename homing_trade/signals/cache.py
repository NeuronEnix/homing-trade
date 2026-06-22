"""One enforced cache-aware contract for every external signal (Phase 6 #6).

All feeds (fng / derivs / coindcx / price_ref / news) read through `cached_signal()` — the SINGLE
audited path, so no feed can drift from the contract:

  * cache every external pull in signal_cache with `fetched_at`;
  * serve a fresh cached value (this is the rate-limiter — at most one refetch per `max_age_sec`
    window; combined with the slow consult cadence, upstreams are polled gently);
  * on ANY fetch failure (None or exception), degrade to the stale cached value, or None — never
    crash the consult.

`signal_status()` makes the cache inspectable (per-row freshness) for the dashboard / debugging.
This module is a leaf: it imports nothing from the rest of the package.
"""
import time


def cached_signal(repo, source, key, fetch_fn, *, ts_fn=None, now=None, max_age_sec):
    """Cache-aware read for one (source, key). `fetch_fn()` takes no args and returns the reading
    or None. Serves the cached value while it is younger than `max_age_sec`; otherwise calls
    `fetch_fn()`; on success caches it (observation ts from `ts_fn(value)`, else `now`) and returns
    it; on None/exception returns the stale cached value (or None). Never raises. Epoch MS."""
    now = int(now if now is not None else time.time() * 1000)
    cached = repo.get_signal(source, key) if hasattr(repo, "get_signal") else None
    if cached and (now - cached["fetched_at"]) < max_age_sec * 1000:
        return cached["value"]
    try:
        fresh = fetch_fn()
    except Exception:
        fresh = None
    if fresh is None:
        return cached["value"] if cached else None
    ts = ts_fn(fresh) if ts_fn is not None else now
    if hasattr(repo, "upsert_signal"):
        repo.upsert_signal(source, key, ts, fresh, now)
    return fresh


def signal_status(repo, *, now=None, max_age_sec=3600):
    """Inspect the signal cache -> [{source, key, fetched_at, age_sec, stale}] over all cached rows
    (newest fetch first). `stale` flags rows older than `max_age_sec` (a coarse global threshold for
    display). Read-only; [] when the repo exposes no cache."""
    if not hasattr(repo, "all_signals"):
        return []
    now = int(now if now is not None else time.time() * 1000)
    out = []
    for row in repo.all_signals():
        age = (now - row["fetched_at"]) / 1000.0
        out.append({"source": row["source"], "key": row["key"], "fetched_at": row["fetched_at"],
                    "age_sec": round(age, 1), "stale": age > max_age_sec})
    return out
