# algotrading/engine.py
import time
from algotrading.config import CONFIG
from algotrading.db import Database
from algotrading.broker import Broker
from algotrading.feed import get_candles
from algotrading.models import Position
from algotrading.skills.ma_trend import MaTrend
from algotrading.skills.rsi_revert import RsiRevert
from algotrading.skills.grid import Grid
from algotrading.skills.rl_qlearn import RLQLearn
from algotrading.skills.committee import Committee

_SKILL_FACTORY = {
    "ma_trend": MaTrend,
    "rsi_revert": RsiRevert,
    "grid": Grid,
    "rl_qlearn": RLQLearn,
    "committee": Committee,
}


def build_skills(names):
    return [_SKILL_FACTORY[n]() for n in names if n in _SKILL_FACTORY]


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


def _open_position(db, broker, skill, side, candle, cfg, now_ms):
    entry_fill = broker.fill_price(candle.close, side, is_entry=True)
    size, margin = broker.position_size(
        db.get_balance(skill.name), entry_fill, cfg.risk_pct, cfg.stop_pct, cfg.leverage)
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
    for skill in skills:
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
            _open_position(db, broker, skill, signal.action, candle, cfg, now_ms)
        elif signal.action == "CLOSE" and position is not None:
            _close_position(db, broker, skill, position, candle.close, candle, now_ms)
        # 4. equity snapshot
        pos_now = db.get_open_position(skill.name)
        unreal = broker.unrealized_pnl(pos_now, candle.close) if pos_now else 0.0
        db.record_equity(skill.name, db.get_balance(skill.name) + unreal, now_ms)


def run(cfg=CONFIG, *, fetcher=None, max_ticks=None, sleeper=None):
    sleeper = sleeper or time.sleep
    db = Database(cfg.db_path)
    broker = Broker(cfg.fee, cfg.slippage)
    skills = build_skills(cfg.enabled_skills)
    for s in skills:
        db.ensure_strategy(s.name, cfg.starting_balance)
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
            ticks += 1
            if max_ticks is None or ticks < max_ticks:
                sleeper(cfg.poll_seconds)
    finally:
        db.close()


if __name__ == "__main__":
    run()
