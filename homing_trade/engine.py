# homing_trade/engine.py
import os
import time
from homing_trade.allocator import compute_allocations, recent_performance
from homing_trade.config import CONFIG
from homing_trade.db import Database
from homing_trade.broker import Broker
from homing_trade.feed import get_candles
from homing_trade.models import Position
from homing_trade.skills.ma_trend import MaTrend
from homing_trade.skills.rsi_revert import RsiRevert
from homing_trade.skills.grid import Grid
from homing_trade.skills.rl_qlearn import RLQLearn
from homing_trade.skills.committee import Committee, build_agents

_SKILL_FACTORY = {
    "ma_trend": MaTrend,
    "rsi_revert": RsiRevert,
    "grid": Grid,
    "rl_qlearn": RLQLearn,
    "committee": Committee,
}


def build_skills(names, cfg=None):
    skills = []
    for n in names:
        if n not in _SKILL_FACTORY:
            continue
        if cfg is not None and n == "rl_qlearn":
            skills.append(RLQLearn(
                alpha=cfg.rl_alpha, gamma=cfg.rl_gamma, epsilon=cfg.rl_epsilon,
                fast=cfg.rl_fast, slow=cfg.rl_slow,
                qtable_path=os.path.join(cfg.qtable_dir, f"qtable_{n}.json")))
        elif cfg is not None and n == "committee":
            skills.append(Committee(agents=build_agents(cfg.agent_mode, cfg),
                                    threshold=cfg.committee_threshold))
        else:
            skills.append(_SKILL_FACTORY[n]())
    return skills


def _close_position(db, broker, skill, position, exit_price, candle, now_ms):
    exit_fill = broker.fill_price(exit_price, position.side, is_entry=False)
    pnl = broker.realized_pnl(position, exit_fill)
    fee = broker.entry_fee(position.size, exit_fill)
    balance = db.get_balance(skill.name) + pnl - fee
    db.set_balance(skill.name, balance)
    db.close_position(position.id)
    db.record_trade(skill.name, position.id, position.side, "CLOSE", exit_fill,
                    position.size, fee, pnl - fee, now_ms)
    return balance


def _open_position(db, broker, skill, side, candle, cfg, now_ms, weight=1.0):
    entry_fill = broker.fill_price(candle.close, side, is_entry=True)
    size, margin = broker.position_size(
        db.get_balance(skill.name), entry_fill, cfg.risk_pct * weight, cfg.stop_pct, cfg.leverage)
    stop = broker.stop_price(entry_fill, side, cfg.stop_pct)
    fee = broker.entry_fee(size, entry_fill)
    balance = db.get_balance(skill.name) - fee
    db.set_balance(skill.name, balance)
    pos = Position(strategy=skill.name, side=side, entry_price=entry_fill, size=size,
                   leverage=cfg.leverage, margin=margin, stop_price=stop, opened_at=candle.time)
    pid = db.open_position(pos)
    db.record_trade(skill.name, pid, side, "OPEN", entry_fill, size, fee, -fee, now_ms)


def process_tick(db, broker, skills, candles, cfg):
    candle = candles[-1]
    now_ms = int(time.time() * 1000)  # wall-clock event time for the audit trail
    if getattr(cfg, "allocator_enabled", False):
        perf = {s.name: recent_performance(db, s.name, cfg.allocator_lookback) for s in skills}
        weights = compute_allocations(perf)
    else:
        weights = {}
    for skill in skills:
        weight = weights.get(skill.name, 1.0)
        position = db.get_open_position(skill.name)
        # 1. risk checks on existing position
        if position is not None:
            if broker.hit_liquidation(position, candle):
                _close_position(db, broker, skill, position,
                                broker.liquidation_price(position), candle, now_ms)
                position = None
            elif broker.hit_stop(position, candle):
                _close_position(db, broker, skill, position, position.stop_price, candle, now_ms)
                position = None
        # 2. strategy decision
        signal = skill.on_candle(candles, position)
        db.log_decision(skill.name, now_ms, candle.time, signal.action,
                        signal.confidence, signal.reason, signal.indicators)
        # 3. act
        if signal.action in ("LONG", "SHORT") and position is None:
            _open_position(db, broker, skill, signal.action, candle, cfg, now_ms, weight)
        elif signal.action == "CLOSE" and position is not None:
            _close_position(db, broker, skill, position, candle.close, candle, now_ms)
        # 4. equity snapshot
        pos_now = db.get_open_position(skill.name)
        unreal = broker.unrealized_pnl(pos_now, candle.close) if pos_now else 0.0
        db.record_equity(skill.name, db.get_balance(skill.name) + unreal, now_ms)


def run(cfg=CONFIG, *, fetcher=None, max_ticks=None, sleeper=None, notifier=None):
    sleeper = sleeper or time.sleep
    db = Database(cfg.db_path)
    broker = Broker(cfg.fee, cfg.slippage)
    skills = build_skills(cfg.enabled_skills, cfg)
    for s in skills:
        db.ensure_strategy(s.name, cfg.starting_balance)
    last_alert_id = 0
    if notifier is not None:
        _row = db.conn.execute("SELECT MAX(id) AS m FROM trades").fetchone()
        last_alert_id = _row["m"] or 0
    ticks = 0
    try:
        while max_ticks is None or ticks < max_ticks:
            try:
                candles = get_candles(cfg.pair_candles, cfg.interval, fetcher=fetcher)
            except Exception as exc:  # transient network/API error: skip this tick, keep looping
                print(f"[feed] fetch failed, skipping tick: {exc}")
                candles = []
            if candles:
                db.save_candles(cfg.pair_candles, cfg.interval, candles, source="live")
                newest = str(candles[-1].time)
                if db.get_state("last_candle_time") != newest:
                    process_tick(db, broker, skills, candles, cfg)
                    db.set_state("last_candle_time", newest)
                    if notifier is not None:
                        for t in db.trades_after(last_alert_id):
                            notifier.notify("trade", f"{t['strategy']} {t['action']}",
                                            f"{t['side']} {t['size']:.6f} @ {t['price']:.2f} pnl={t['pnl']:.2f}")
                            last_alert_id = t["id"]
            ticks += 1
            if max_ticks is None or ticks < max_ticks:
                sleeper(cfg.poll_seconds)
    finally:
        for s in skills:
            if hasattr(s, "save"):
                try:
                    s.save()
                except Exception:
                    pass
        db.close()


if __name__ == "__main__":
    run()
