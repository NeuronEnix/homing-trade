import math

CANDLE_INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "8h": 28_800_000, "1d": 86_400_000, "3d": 259_200_000, "1w": 604_800_000,
}

_MS_PER_YEAR = 365 * 86_400_000


def periods_per_year(interval: str) -> float:
    return _MS_PER_YEAR / CANDLE_INTERVAL_MS[interval]


def _closed(trades):
    return [t for t in trades if t["action"] == "CLOSE"]


def total_return_pct(start_balance: float, final_equity: float) -> float:
    if start_balance == 0:
        return 0.0
    return (final_equity - start_balance) / start_balance * 100


def win_rate(trades) -> float:
    closed = _closed(trades)
    if not closed:
        return 0.0
    return sum(1 for t in closed if t["pnl"] > 0) / len(closed)


def profit_factor(trades) -> float:
    closed = _closed(trades)
    gross_profit = sum(t["pnl"] for t in closed if t["pnl"] > 0)
    gross_loss = -sum(t["pnl"] for t in closed if t["pnl"] < 0)
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def avg_win(trades) -> float:
    wins = [t["pnl"] for t in _closed(trades) if t["pnl"] > 0]
    return sum(wins) / len(wins) if wins else 0.0


def avg_loss(trades) -> float:
    losses = [t["pnl"] for t in _closed(trades) if t["pnl"] < 0]
    return sum(losses) / len(losses) if losses else 0.0


def max_drawdown(equity_curve) -> float:
    peak = None
    max_dd = 0.0
    for _, eq in equity_curve:
        if peak is None or eq > peak:
            peak = eq
        if peak and peak > 0:
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def sharpe(equity_curve, periods_per_year_value: float) -> float:
    eqs = [eq for _, eq in equity_curve]
    if len(eqs) < 2:
        return 0.0
    rets = []
    for i in range(1, len(eqs)):
        prev = eqs[i - 1]
        if prev != 0:
            rets.append((eqs[i] - prev) / prev)
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(periods_per_year_value)
