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
