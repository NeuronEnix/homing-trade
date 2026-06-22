"""Continuous walk-forward backtest job (Phase 7 #7).

Runs `walk_forward` over the enabled + candidate strategies on a slow wall-clock cadence and records
each strategy's OOS aggregate AND its trusted (post-cutoff, profit-mirage-guarded) subset to SQLite,
so the dashboard always shows fresh, honest out-of-sample evidence — and candidate strategies
accumulate the OOS track record the promotion gate (Phase 7 #5) needs.

Gated like the reflection/research loops: a no-op unless `continuous_backtest_enabled`, self-paced
(consults at most every poll_sec), and isolated — one strategy failing never blocks the others or the
engine loop. It pulls ONLY already-stored candles (no network inside the tick). It runs synchronously
on its own cadence, so a large stored history × many strategies can make one tick slow; the daily
default cadence keeps that rare and it is opt-in.
"""
import time

from homing_trade.walkforward import walk_forward
from homing_trade.profit_mirage import cutoff_ms_from_iso

_DAY_MS = 86_400_000
CANDIDATE_STRATEGIES = ("supertrend", "zscore_revert", "vol_breakout", "ttm_squeeze")


class BacktestRunner:
    def __init__(self, repo, cfg, *, candle_provider=None, poll_sec=None, clock=None,
                 strategies=None, enabled=None):
        self.repo = repo
        self.cfg = cfg
        self.enabled = getattr(cfg, "continuous_backtest_enabled", False) if enabled is None else enabled
        self.poll_sec = poll_sec if poll_sec is not None else \
            getattr(cfg, "continuous_backtest_poll_sec", _DAY_MS // 1000)
        self._clock = clock or time.time
        self._last = None
        self.candle_provider = candle_provider or self._default_candle_provider
        # enabled mechanical strategies + the Phase-7 candidates (deduped, order-preserving)
        self.strategies = list(dict.fromkeys(list(cfg.enabled_skills) + list(CANDIDATE_STRATEGIES))) \
            if strategies is None else list(strategies)
        self.cutoff_ms = cutoff_ms_from_iso(getattr(cfg, "trust_cutoff_iso", ""))

    def _default_candle_provider(self):
        now_ms = int(self._clock() * 1000)
        start = now_ms - getattr(self.cfg, "continuous_backtest_days", 365) * _DAY_MS
        return self.repo.get_candles_range(self.cfg.pair_candles, self.cfg.interval, start, now_ms,
                                           source="all")

    def run(self, now_ms=None):
        """Run + record a backtest for each strategy if the cadence is due. Returns the per-strategy
        summaries (possibly empty). A no-op when disabled; never raises."""
        if not self.enabled:
            return []
        now = self._clock()
        if self._last is not None and (now - self._last) < self.poll_sec:
            return []
        # Fetch candles BEFORE stamping the cadence: a transient empty read / error (cold start,
        # history not yet backfilled) must NOT burn a full poll_sec — retry on the next tick.
        try:
            candles = self.candle_provider()
        except Exception:
            return []
        if not candles:
            return []
        self._last = now                       # stamp before the (slow) run so it still spaces out
        ts = now_ms if now_ms is not None else int(now * 1000)
        from homing_trade.engine import build_skills       # lazy import: avoid an engine<->job cycle
        train = getattr(self.cfg, "continuous_backtest_train", 500)
        test = getattr(self.cfg, "continuous_backtest_test", 200)
        window = getattr(self.cfg, "continuous_backtest_window", 200)
        out = []
        for name in self.strategies:
            try:
                # An unknown name makes the factory's build_skills([name]) return [] -> [0] raises,
                # caught here and skipped (no separate existence check / redundant build).
                wf = walk_forward(lambda n=name: build_skills([n], self.cfg)[0], candles, self.cfg,
                                  self.cfg.starting_balance, train=train, test=test, window=window,
                                  cutoff_ms=self.cutoff_ms)
                rid = self.repo.record_backtest_result(
                    ts, name, pair=self.cfg.pair_candles, interval=self.cfg.interval, train=train,
                    test=test, window=window, cutoff_ms=self.cutoff_ms,
                    oos=wf["oos"], trusted=wf["trusted_oos"])
                out.append({"strategy": name, "id": rid, "oos": wf["oos"],
                            "trusted_oos": wf["trusted_oos"]})
            except Exception:
                continue                       # one strategy failing never blocks the rest / the loop
        return out


def main(argv=None, cfg=None):
    import argparse
    from homing_trade.config import CONFIG
    from homing_trade.repository import Repository

    cfg = cfg or CONFIG
    p = argparse.ArgumentParser(
        description="Run the continuous walk-forward backtest job once (cron entry).")
    p.add_argument("--skill", default=None, help="one strategy (default: enabled + candidates)")
    p.add_argument("--days", type=int, default=getattr(cfg, "continuous_backtest_days", 365))
    args = p.parse_args(argv)

    from dataclasses import replace
    run_cfg = replace(cfg, continuous_backtest_days=args.days)
    repo = Repository.open(cfg.db_path)
    try:
        strategies = [args.skill] if args.skill else None
        runner = BacktestRunner(repo, run_cfg, enabled=True, poll_sec=0, strategies=strategies)
        rows = runner.run()
        if not rows:
            print("No backtest results (continuous_backtest disabled? no stored candles?).")
            return
        print(f"{'strategy':<14} {'OOS ret%':>9} {'OOS sharpe':>11} {'trusted ret%':>13} "
              f"{'trusted folds':>14}")
        for r in rows:
            o, t = r["oos"], r["trusted_oos"]
            print(f"{r['strategy']:<14} {o['compounded_return_pct']:>8.2f}% {o['mean_sharpe']:>11.2f} "
                  f"{t['compounded_return_pct']:>12.2f}% {t['folds']:>14}")
    finally:
        repo.close()


if __name__ == "__main__":
    main()
