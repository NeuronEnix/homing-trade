from homing_trade.feed import parse_candles, CANDLES_URL, _http_fetcher
from homing_trade.metrics import CANDLE_INTERVAL_MS

_DAY_MS = 86_400_000
_MAX_LIMIT = 1000


def ensure_history(db, pair, interval, days, now_ms, *, fetcher=None):
    fetcher = fetcher or _http_fetcher
    step = CANDLE_INTERVAL_MS[interval]
    end = (now_ms // step) * step - step                 # last completed candle, aligned
    start = ((now_ms - days * _DAY_MS) // step) * step    # aligned range start
    bounds = db.get_candle_bounds(pair, interval)
    if bounds is None:
        spans = [(start, end)]
    else:
        mn, mx = bounds
        spans = []
        if start < mn:
            spans.append((start, mn - step))
        if end > mx:
            spans.append((mx + step, end))
    for span_start, span_end in spans:
        _fetch_span(db, pair, interval, span_start, span_end, step, fetcher)
    return db.get_candles_range(pair, interval, start, end, source="all")


def _fetch_span(db, pair, interval, span_start, span_end, step, fetcher):
    cursor = span_start
    while cursor <= span_end:
        chunk_end = min(cursor + step * (_MAX_LIMIT - 1), span_end)
        params = {"pair": pair, "interval": interval,
                  "startTime": cursor, "endTime": chunk_end, "limit": _MAX_LIMIT}
        try:
            raw = fetcher(CANDLES_URL, params)
        except Exception as exc:
            print(f"[history] fetch failed at {cursor}, keeping stored data: {exc}")
            return
        candles = parse_candles(raw)
        if not candles:
            # No data this far back (CoinDCX serves only recent history for some pairs).
            # Probe forward in ~200-candle windows instead of giving up, so we still fetch
            # whatever recent history IS available.
            cursor += step * 200
            continue
        db.save_candles(pair, interval, candles, source="history")
        last_time = candles[-1].time
        if last_time < cursor:  # no forward progress; stop to avoid an infinite loop
            break
        cursor = last_time + step
