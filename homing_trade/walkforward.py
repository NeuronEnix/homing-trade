"""Walk-forward evaluation harness (Phase 7 #1).

Honest out-of-sample (OOS) evaluation on top of `backtest.run_backtest`. The roadmap discipline: a
change is "learned" only if it holds out-of-sample. So we roll a train/test split forward over the
candle series; for each fold we (optionally) FIT — freeze params/prompt/playbook — on the TRAIN
slice, then EVALUATE on the next UNSEEN TEST slice. The aggregate OOS summary stitches the per-fold
test results into one compounded curve.

No lookahead: a fold's test result depends only on candles up to its test window's end. `fit_fn` sees
ONLY its train slice; evaluation sees ONLY its test slice. Mechanical strategies have no tunable
params yet, so the default fit is identity and the harness simply measures honest rolling OOS
performance — but `fit_fn` is the hook where a future tuned param/prompt/playbook search plugs in, and
it is forced through this same train→freeze→test gate, so an in-sample fit can never inflate the
reported OOS metrics.

Caveat (documented, conservative — never a leak): each test fold is evaluated in isolation, so a
strategy's indicators warm up WITHIN the test slice rather than carrying state from the train slice.
That slightly understates early-fold performance; it never uses future data. Threading a warm-up
lookback through `run_backtest` is a noted follow-on.
"""
from homing_trade.backtest import run_backtest
from homing_trade import metrics
from homing_trade.profit_mirage import is_post_cutoff, partition_folds_by_trust

_DAY_MS = 86_400_000


def fold_bounds(n, train, test, step=None):
    """Tile [0, n) into (train_lo, train_hi, test_lo, test_hi) folds; `*_hi` are exclusive.

    `step` defaults to `test`, giving contiguous, non-overlapping OOS test windows. A fold is emitted
    only when its full train+test span fits, so no fold ever runs past the available candles."""
    if train <= 0 or test <= 0:
        raise ValueError("train and test must be positive")
    step = test if step is None else step
    if step <= 0:
        raise ValueError("step must be positive")
    folds, lo = [], 0
    while lo + train + test <= n:
        folds.append((lo, lo + train, lo + train, lo + train + test))
        lo += step
    return folds


def _aggregate_oos(fold_results, starting_balance):
    """Stitch per-fold OOS test results into one compounded summary."""
    if not fold_results:
        return {"folds": 0, "compounded_return_pct": 0.0, "final_equity": starting_balance,
                "mean_return_pct": 0.0, "hit_rate": 0.0, "total_trades": 0, "mean_sharpe": 0.0,
                "worst_drawdown": 0.0, "worst_return_pct": 0.0}
    returns = [f["return_pct"] for f in fold_results]
    equity = starting_balance
    for r in returns:
        equity *= (1 + r / 100.0)                          # compound fold-over-fold
    nf = len(fold_results)
    return {
        "folds": nf,
        "compounded_return_pct": metrics.total_return_pct(starting_balance, equity),
        "final_equity": equity,
        "mean_return_pct": sum(returns) / nf,
        "hit_rate": sum(1 for r in returns if r > 0) / nf,   # fraction of OOS folds that made money
        "total_trades": sum(f["trades"] for f in fold_results),
        "mean_sharpe": sum(f["sharpe"] for f in fold_results) / nf,
        "worst_drawdown": max(f["max_drawdown"] for f in fold_results),
        "worst_return_pct": min(returns),
    }


def walk_forward(skill_factory, candles, cfg, starting_balance, *, train, test, step=None,
                 window=200, fit_fn=None, cutoff_ms=None):
    """Roll a train/test split forward over `candles`; evaluate each test window out-of-sample.

    skill_factory: a zero-arg callable returning a FRESH skill per fold (e.g. the skill CLASS), so no
        per-fold state bleeds across folds. MUST be callable — a bare skill instance is rejected,
        because reusing one instance would let a stateful skill (Q-table, grid state) carry learning
        across folds and across the fit→eval boundary, which is exactly the leak this harness prevents.
    fit_fn(skill, train_candles, cfg) -> cfg' : freeze params on the train slice and return the cfg to
        evaluate the test slice with. Default: identity (no fit) — returning a falsy value also keeps
        the original cfg. fit_fn must NOT look beyond `train_candles`.
    cutoff_ms: the profit-mirage trust cutoff (Phase 7 #6). When given, each fold is tagged
        `post_cutoff` (its test window entirely after the cutoff) and a `trusted_oos` aggregate is
        computed over ONLY the post-cutoff folds — pre-cutoff folds may be a memorized-data mirage.

    Returns {"strategy", "train", "test", "step", "window", "cutoff_ms", "folds": [per-fold result...],
             "oos": {all folds}, "trusted_oos": {post-cutoff folds only}}. Each per-fold result is
    run_backtest's dict plus {"fold", "train_range", "test_range", "post_cutoff"}.
    """
    if not callable(skill_factory):
        raise TypeError("skill_factory must be a zero-arg callable returning a FRESH skill per fold "
                        "(e.g. the skill class), not a bare instance — reuse would bleed state "
                        "across folds and defeat the no-lookahead guarantee")
    factory = skill_factory
    eff_step = test if step is None else step
    name = factory().name
    fold_results = []
    for k, (tr_lo, tr_hi, te_lo, te_hi) in enumerate(fold_bounds(len(candles), train, test, step)):
        fold_cfg = cfg
        if fit_fn is not None:
            fold_cfg = fit_fn(factory(), candles[tr_lo:tr_hi], cfg) or cfg
        skill = factory()                                  # fresh skill for the OOS evaluation
        res = run_backtest(skill, candles[te_lo:te_hi], fold_cfg, starting_balance, window=window)
        res = {**res, "fold": k,
               "train_range": (candles[tr_lo].time, candles[tr_hi - 1].time),
               "test_range": (candles[te_lo].time, candles[te_hi - 1].time),
               "post_cutoff": is_post_cutoff(candles[te_lo].time, cutoff_ms)}
        fold_results.append(res)
    trusted, _ = partition_folds_by_trust(fold_results, cutoff_ms)
    return {"strategy": name, "train": train, "test": test, "step": eff_step, "window": window,
            "cutoff_ms": cutoff_ms, "folds": fold_results,
            "oos": _aggregate_oos(fold_results, starting_balance),
            "trusted_oos": _aggregate_oos(trusted, starting_balance)}


def _format(out):
    """Human-readable per-fold table + the stitched OOS summary for one strategy."""
    o = out["oos"]
    lines = [f"{out['strategy']}  train={out['train']} test={out['test']} step={out['step']} "
             f"window={out['window']}  folds={o['folds']}"]
    hdr = f"  {'fold':>4} {'test_return%':>13} {'trades':>7} {'sharpe':>8} {'maxDD%':>8}"
    lines += [hdr, "  " + "-" * (len(hdr) - 2)]
    for f in out["folds"]:
        lines.append(f"  {f['fold']:>4} {f['return_pct']:>12.2f}% {f['trades']:>7} "
                     f"{f['sharpe']:>8.2f} {f['max_drawdown'] * 100:>7.2f}%")
    if o["folds"]:
        lines.append(f"  OOS (all): compounded={o['compounded_return_pct']:.2f}%  "
                     f"hit_rate={o['hit_rate'] * 100:.0f}%  trades={o['total_trades']}  "
                     f"mean_sharpe={o['mean_sharpe']:.2f}  worstDD={o['worst_drawdown'] * 100:.2f}%  "
                     f"worst_fold={o['worst_return_pct']:.2f}%")
    else:
        lines.append("  OOS: no folds (not enough candles for one train+test span)")
    # Profit-mirage guard: the trusted (post-cutoff) subset is the only evidence to act on.
    t = out.get("trusted_oos")
    if out.get("cutoff_ms") is not None and t is not None:
        if t["folds"]:
            lines.append(f"  TRUSTED (post-cutoff {t['folds']}/{o['folds']} folds): "
                         f"compounded={t['compounded_return_pct']:.2f}%  "
                         f"hit_rate={t['hit_rate'] * 100:.0f}%  mean_sharpe={t['mean_sharpe']:.2f}")
        else:
            lines.append("  TRUSTED: no post-cutoff folds — all OOS data predates the cutoff (mirage risk)")
    return "\n".join(lines)


def main(argv=None, cfg=None):
    import argparse
    import time
    from dataclasses import replace
    from homing_trade.config import CONFIG
    from homing_trade.repository import Repository
    from homing_trade.engine import build_skills
    from homing_trade.history import ensure_history
    from homing_trade.profit_mirage import cutoff_ms_from_iso

    cfg = cfg or CONFIG
    p = argparse.ArgumentParser(
        description="Walk-forward (honest out-of-sample) evaluation of paper strategies.")
    p.add_argument("--skill", default=None, help="strategy name (default: all enabled)")
    p.add_argument("--days", type=int, default=120)
    p.add_argument("--interval", default=cfg.interval)
    p.add_argument("--source", choices=["all", "live", "history"], default="all")
    p.add_argument("--balance", type=float, default=cfg.starting_balance)
    p.add_argument("--train", type=int, default=500, help="train-window candles per fold")
    p.add_argument("--test", type=int, default=200, help="out-of-sample test-window candles per fold")
    p.add_argument("--step", type=int, default=None, help="fold stride (default: test → no overlap)")
    p.add_argument("--window", type=int, default=200, help="indicator lookback inside run_backtest")
    p.add_argument("--cutoff", default=getattr(cfg, "trust_cutoff_iso", ""),
                   help="profit-mirage trust cutoff (ISO UTC); only post-cutoff folds are trusted")
    args = p.parse_args(argv)

    names = [args.skill] if args.skill else list(cfg.enabled_skills)
    run_cfg = replace(cfg, interval=args.interval)
    cutoff_ms = cutoff_ms_from_iso(args.cutoff)
    repo = Repository.open(cfg.db_path)
    try:
        now_ms = int(time.time() * 1000)
        ensure_history(repo, cfg.pair_candles, args.interval, args.days, now_ms)
        candles = repo.get_candles_range(cfg.pair_candles, args.interval,
                                         now_ms - args.days * _DAY_MS, now_ms, source=args.source)
        print(f"Walk-forward: {cfg.pair_candles} {args.interval}  last {args.days}d  "
              f"source={args.source}  candles={len(candles)}  trust_cutoff={args.cutoff or '(none)'}")
        for n in names:
            out = walk_forward(lambda n=n: build_skills([n])[0], candles, run_cfg, args.balance,
                               train=args.train, test=args.test, step=args.step, window=args.window,
                               cutoff_ms=cutoff_ms)
            print(_format(out))
        print("Note: only TRUSTED (post-cutoff, walk-forward) results count as evidence — pre-cutoff "
              "folds may be a memorized-data mirage; an in-sample fit cannot inflate these either.")
    finally:
        repo.close()


if __name__ == "__main__":
    main()
