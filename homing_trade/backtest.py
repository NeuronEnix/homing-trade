# homing_trade/backtest.py
import argparse
import time
from dataclasses import replace
from homing_trade.config import CONFIG
from homing_trade.repository import Repository
from homing_trade.broker import Broker
from homing_trade.engine import build_skills, process_tick
from homing_trade.ledger import MemoryLedger
from homing_trade.history import ensure_history
from homing_trade import metrics

_DAY_MS = 86_400_000


def run_backtest(skill, candles, cfg, starting_balance, window=200):
    ledger = MemoryLedger(skill.name, starting_balance)
    broker = Broker(cfg.fee, cfg.slippage)
    n = len(candles)
    for i in range(1, n + 1):
        win = candles[max(0, i - window):i]
        process_tick(ledger, broker, [skill], win, cfg)
    curve = [(candles[k].time, eq) for k, (_, eq) in enumerate(ledger.equity_curve)]
    final_equity = curve[-1][1] if curve else starting_balance
    ppy = metrics.periods_per_year(cfg.interval)
    return {
        "strategy": skill.name,
        "trades": len([t for t in ledger.trades if t["action"] == "CLOSE"]),
        "final_equity": final_equity,
        "return_pct": metrics.total_return_pct(starting_balance, final_equity),
        "win_rate": metrics.win_rate(ledger.trades),
        "profit_factor": metrics.profit_factor(ledger.trades),
        "max_drawdown": metrics.max_drawdown(ledger.equity_curve),
        "sharpe": metrics.sharpe(ledger.equity_curve, ppy),
        "avg_win": metrics.avg_win(ledger.trades),
        "avg_loss": metrics.avg_loss(ledger.trades),
        "equity_curve": curve,
    }


def _format(results):
    header = (f"{'strategy':<12} {'trades':>7} {'return%':>9} {'sharpe':>8} "
              f"{'maxDD%':>7} {'win%':>6} {'PF':>7}")
    lines = [header, "-" * len(header)]
    for r in sorted(results, key=lambda x: x["return_pct"], reverse=True):
        pf = r["profit_factor"]
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        lines.append(f"{r['strategy']:<12} {r['trades']:>7} {r['return_pct']:>8.2f}% "
                     f"{r['sharpe']:>8.2f} {r['max_drawdown'] * 100:>6.2f}% "
                     f"{r['win_rate'] * 100:>5.1f}% {pf_s:>7}")
    return "\n".join(lines)


def _write_csv(path, results):
    with open(path, "w", encoding="utf-8") as f:
        f.write("time,strategy,equity\n")
        for r in results:
            for ts, eq in r["equity_curve"]:
                f.write(f"{ts},{r['strategy']},{eq}\n")


def main(argv=None, cfg=CONFIG):
    parser = argparse.ArgumentParser(
        description="Backtest paper-trading strategies on stored CoinDCX candles.")
    parser.add_argument("--skill", default=None, help="strategy name (default: all enabled)")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--interval", default=cfg.interval)
    parser.add_argument("--source", choices=["all", "live", "history"], default="all")
    parser.add_argument("--balance", type=float, default=cfg.starting_balance)
    parser.add_argument("--csv", default=None)
    args = parser.parse_args(argv)

    names = [args.skill] if args.skill else list(cfg.enabled_skills)
    skills = build_skills(names)
    run_cfg = replace(cfg, interval=args.interval)

    repo = Repository.open(cfg.db_path)
    try:
        now_ms = int(time.time() * 1000)
        ensure_history(repo, cfg.pair_candles, args.interval, args.days, now_ms)
        start = (now_ms - args.days * _DAY_MS)
        candles = repo.get_candles_range(cfg.pair_candles, args.interval, start, now_ms,
                                         source=args.source)
        results = [run_backtest(s, candles, run_cfg, args.balance) for s in skills]
        print(f"Backtest: {cfg.pair_candles} {args.interval}  last {args.days}d  "
              f"source={args.source}  candles={len(candles)}")
        print(_format(results))
        print("Note: backtest results can overfit — confirm with live paper "
              "trading before going live.")
        if args.csv:
            _write_csv(args.csv, results)
    finally:
        repo.close()


if __name__ == "__main__":
    main()
