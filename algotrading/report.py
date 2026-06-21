from algotrading.config import CONFIG
from algotrading.db import Database


def compute_stats(db: Database, strategy: str, starting_balance: float) -> dict:
    closed = db.conn.execute(
        "SELECT pnl FROM trades WHERE strategy=? AND action='CLOSE'", (strategy,)
    ).fetchall()
    pnls = [float(r["pnl"]) for r in closed]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    trades = len(pnls)
    realized = sum(pnls)
    win_rate = (wins / trades) if trades else 0.0

    eq_rows = db.conn.execute(
        "SELECT equity FROM equity WHERE strategy=? ORDER BY ts ASC", (strategy,)
    ).fetchall()
    curve = [float(r["equity"]) for r in eq_rows]
    equity = curve[-1] if curve else db.get_balance(strategy)

    peak = starting_balance
    max_dd = 0.0
    for e in curve:
        peak = max(peak, e)
        dd = (peak - e) / peak if peak else 0.0
        max_dd = max(max_dd, dd)

    return {
        "strategy": strategy,
        "equity": equity,
        "return_pct": (equity - starting_balance) / starting_balance * 100,
        "realized_pnl": realized,
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "max_drawdown": max_dd,
    }


def leaderboard(db: Database, strategies: list[str], starting_balance: float) -> list[dict]:
    rows = [compute_stats(db, s, starting_balance) for s in strategies]
    rows.sort(key=lambda r: r["equity"], reverse=True)
    return rows


def format_leaderboard(rows: list[dict]) -> str:
    header = f"{'strategy':<12} {'equity':>10} {'return%':>9} {'trades':>7} {'win%':>6} {'maxDD%':>7}"
    lines = [header, "-" * len(header)]
    for r in rows:
        lines.append(
            f"{r['strategy']:<12} {r['equity']:>10.2f} {r['return_pct']:>8.2f}% "
            f"{r['trades']:>7} {r['win_rate']*100:>5.1f}% {r['max_drawdown']*100:>6.2f}%"
        )
    return "\n".join(lines)


def main(cfg=CONFIG) -> None:
    db = Database(cfg.db_path)
    try:
        rows = leaderboard(db, cfg.enabled_skills, cfg.starting_balance)
        print(format_leaderboard(rows))
    finally:
        db.close()


if __name__ == "__main__":
    main()
