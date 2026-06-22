def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    seed = sum(values[:period]) / period
    e = seed
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(values, fast=12, slow=26, signal=9):
    """Return (macd_line, signal_line) at the latest point, or (None, None) if short."""
    if len(values) < slow + signal:
        return None, None
    macd_series = []
    for i in range(slow, len(values) + 1):
        f, s = ema(values[:i], fast), ema(values[:i], slow)
        if f is not None and s is not None:
            macd_series.append(f - s)
    if len(macd_series) < signal:
        return None, None
    return macd_series[-1], ema(macd_series, signal)


def bollinger(values, period=20, num_std=2.0):
    """Return (mid, upper, lower) over the last `period` values, or (None, None, None)."""
    if len(values) < period:
        return None, None, None
    window = values[-period:]
    mid = sum(window) / period
    var = sum((v - mid) ** 2 for v in window) / period
    sd = var ** 0.5
    return mid, mid + num_std * sd, mid - num_std * sd


def true_ranges(candles):
    """Wilder's true range for each candle after the first: max(H-L, |H-prevC|, |L-prevC|)."""
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i].high, candles[i].low, candles[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return trs


def atr(candles, period: int = 14) -> float | None:
    """Wilder's Average True Range over OHLC candles, or None if too short (< period+1)."""
    if len(candles) < period + 1:
        return None
    trs = true_ranges(candles)
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return a


# --- regime detection (used to tag decisions with their market context) ---

def _wilder_smooth(seq, period):
    """Wilder's running sum smoothing: first value = sum of first `period`, then
    sm = sm - sm/period + x. Returns the smoothed series (len = len(seq)-period+1)."""
    if len(seq) < period:
        return []
    sm = sum(seq[:period])
    out = [sm]
    for v in seq[period:]:
        sm = sm - sm / period + v
        out.append(sm)
    return out


def adx(candles, period: int = 14):
    """Wilder's ADX (trend strength, 0..100) over OHLC candles, or None if too short."""
    if len(candles) < 2 * period + 1:
        return None
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, len(candles)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    atr_s = _wilder_smooth(trs, period)
    pdm_s = _wilder_smooth(plus_dm, period)
    mdm_s = _wilder_smooth(minus_dm, period)
    dxs = []
    for atr_v, pdm_v, mdm_v in zip(atr_s, pdm_s, mdm_s):
        if atr_v == 0:
            dxs.append(0.0)
            continue
        pdi = 100 * pdm_v / atr_v
        mdi = 100 * mdm_v / atr_v
        denom = pdi + mdi
        dxs.append(100 * abs(pdi - mdi) / denom if denom != 0 else 0.0)
    if len(dxs) < period:
        return None
    adx_v = sum(dxs[:period]) / period
    for dx in dxs[period:]:
        adx_v = (adx_v * (period - 1) + dx) / period
    return adx_v


def ema_slope(values, period: int = 20, lookback: int = 5):
    """Fractional change of an EMA over the last `lookback` bars (>0 up, <0 down), or None."""
    if len(values) < period + lookback:
        return None
    now = ema(values, period)
    past = ema(values[:-lookback], period)
    if now is None or past is None or past == 0:
        return None
    return (now - past) / abs(past)


def realized_vol(values, window: int = 20):
    """Sample stddev of the last `window` simple returns, or None if too short."""
    if len(values) < window + 1:
        return None
    rets = []
    for i in range(len(values) - window, len(values)):
        prev = values[i - 1]
        if prev != 0:
            rets.append((values[i] - prev) / prev)
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return var ** 0.5


def classify_regime(candles, *, period: int = 14, trend: float = 25.0, chop: float = 20.0,
                    slope_period: int = 20, vol_window: int = 20) -> dict:
    """Tag the current market context: regime label + the ADX / EMA-slope / realized-vol it
    was derived from. regime ∈ {trend_up, trend_down, chop, transition, unknown}."""
    closes = [c.close for c in candles]
    a = adx(candles, period)
    slope = ema_slope(closes, slope_period)
    vol = realized_vol(closes, vol_window)
    if a is None:
        regime = "unknown"
    elif a >= trend:
        # Strong trend; use the EMA slope for direction. If slope is undetermined
        # (too few bars), don't guess a direction — call it a transition.
        regime = "transition" if slope is None else ("trend_up" if slope > 0 else "trend_down")
    elif a <= chop:
        regime = "chop"
    else:
        regime = "transition"
    return {"regime": regime, "adx": a, "ema_slope": slope, "realized_vol": vol}
