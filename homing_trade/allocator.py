import math


def compute_allocations(perf, *, floor=0.1, cap=1.0):
    names = list(perf.keys())
    if not names:
        return {}
    scores = [perf[n] for n in names]
    m = max(scores)
    exps = [math.exp(s - m) for s in scores]
    total = sum(exps)
    return {n: floor + (cap - floor) * (e / total) for n, e in zip(names, exps)}


def recent_performance(store, strategy, lookback=20):
    pnls = store.recent_close_pnls(strategy, lookback)
    return sum(pnls) / len(pnls) if pnls else 0.0
