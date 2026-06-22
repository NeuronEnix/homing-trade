"""SelfQuery — a strictly read-only "how did I do?" layer over the ledger.

The autonomous learn->correct loop (Phase 4) and the UI ask performance questions through
this: per-strategy win rate / profit factor / Sharpe / drawdown / expectancy, plus
risk-guard activity and the intended-vs-taken decision breakdown.

It only READS — it calls the Repository's read methods + `metrics.py`, and exposes no write
path, so it can never mutate the audit-truth tables (Hierarchy of Truth). Closed trades and
equity snapshots are realized, past events, so there is no look-ahead here; the outcome
embargo becomes relevant once `trade_outcomes` lands (it carries `realized_at_ts`).
"""
from homing_trade import metrics


def _finite(x):
    """Map a non-finite metric (e.g. profit_factor with zero losses -> inf) to None, so
    results stay JSON-safe and downstream code treats it as 'undefined', not a huge score."""
    return None if x in (float("inf"), float("-inf")) else x


class SelfQuery:
    def __init__(self, repo, starting_balance=5000.0):
        self._repo = repo
        self._start = starting_balance

    def performance(self, strategy) -> dict:
        """Performance summary for one strategy, computed from its closed trades + equity."""
        pnls = self._repo.closed_pnls(strategy)
        trades = [{"action": "CLOSE", "pnl": p} for p in pnls]  # adapt to metrics.py shape
        curve = [(0, eq) for eq in self._repo.equity_series(strategy)]
        equity = curve[-1][1] if curve else self._repo.get_balance(strategy)
        return {
            "strategy": strategy,
            "trades": len(pnls),
            "realized_pnl": sum(pnls),
            "equity": equity,
            "return_pct": metrics.total_return_pct(self._start, equity),
            "win_rate": metrics.win_rate(trades),
            "profit_factor": _finite(metrics.profit_factor(trades)),
            "avg_win": metrics.avg_win(trades),
            "avg_loss": metrics.avg_loss(trades),
            "expectancy": (sum(pnls) / len(pnls)) if pnls else 0.0,
            "max_drawdown": metrics.max_drawdown(curve),
            # Raw (non-annualized) Sharpe over the equity-snapshot series — a directional signal.
            "sharpe": metrics.sharpe(curve, 1.0),
        }

    def leaderboard(self, strategies) -> list:
        rows = [self.performance(s) for s in strategies]
        rows.sort(key=lambda r: r["equity"], reverse=True)
        return rows

    def risk_event_counts(self, limit=500) -> dict:
        """Counts of recent risk-guard events by kind (e.g. {'veto': 3, 'halt': 1})."""
        counts = {}
        for ev in self._repo.recent_risk_events(limit):
            counts[ev["kind"]] = counts.get(ev["kind"], 0) + 1
        return counts

    def decision_breakdown(self, strategy) -> dict:
        """How the strategy's decisions resolved: {taken_action: count}."""
        return self._repo.taken_action_counts(strategy)
