# homing_trade/engine.py
import os
import time
from homing_trade.allocator import compute_allocations, recent_performance
from homing_trade.config import CONFIG
from homing_trade.risk import DailyRiskGuard
from homing_trade.repository import Repository
from homing_trade.broker import Broker
from homing_trade.feed import get_candles
from homing_trade.position_manager import PositionManager
from homing_trade.skills.ma_trend import MaTrend
from homing_trade.skills.rsi_revert import RsiRevert
from homing_trade.skills.grid import Grid
from homing_trade.skills.rl_qlearn import RLQLearn
from homing_trade.skills.committee import Committee, build_agents
from homing_trade.skills.macd import MacdCross
from homing_trade.skills.bollinger import BollingerRevert
from homing_trade.skills.donchian import DonchianBreakout
from homing_trade.ai_traders import build_ai_traders

_SKILL_FACTORY = {
    "ma_trend": MaTrend,
    "rsi_revert": RsiRevert,
    "grid": Grid,
    "macd": MacdCross,
    "bollinger": BollingerRevert,
    "donchian": DonchianBreakout,
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


def _close_position(db, broker, skill, position, exit_price, candle, now_ms, guard=None):
    # Backward-compatible shim: the mechanics now live in PositionManager.
    return PositionManager(db, broker, guard=guard).close(skill, position, exit_price, candle, now_ms)


def _open_position(db, broker, skill, side, candle, cfg, now_ms, weight=1.0, guard=None):
    # Backward-compatible shim: the mechanics now live in PositionManager.
    return PositionManager(db, broker, cfg, guard).open(skill, side, candle, now_ms, weight)


def process_tick(db, broker, skills, candles, cfg, guard=None, notifier=None, is_paused=None):
    candle = candles[-1]
    now_ms = int(time.time() * 1000)  # wall-clock event time for the audit trail
    paused = bool(is_paused and is_paused())  # when paused: manage/close existing, open nothing new
    pm = PositionManager(db, broker, cfg, guard)
    if getattr(cfg, "allocator_enabled", False):
        perf = {s.name: recent_performance(db, s.name, cfg.allocator_lookback) for s in skills}
        weights = compute_allocations(perf)
    else:
        weights = {}
    for skill in skills:
        weight = weights.get(skill.name, 1.0)
        # 1. risk checks on any existing position (stop / liquidation)
        position = pm.manage_risk(skill, db.get_open_position(skill.name), candle, now_ms)
        # 2. strategy decision
        signal = skill.on_candle(candles, position)
        db.log_decision(skill.name, now_ms, candle.time, signal.action,
                        signal.confidence, signal.reason, signal.indicators)
        # 2b. persist the full AI response + reasoning (only on a real consult or an error)
        if signal.raw or signal.error:
            m = signal.meta or {}
            db.record_llm_response(skill.name, now_ms, getattr(skill, "backend", ""),
                                   getattr(skill, "model", ""), signal.action, signal.confidence,
                                   m.get("observation", ""), m.get("prediction", ""),
                                   m.get("rationale", ""), signal.raw or "", signal.error or "")
        # 2c. alert on an AI error (deduped so a persistent failure doesn't spam Discord)
        if signal.error and notifier is not None:
            if getattr(skill, "_last_alerted_error", None) != signal.error:
                notifier.notify("error", f"{skill.name} — Claude error", signal.error[:400])
                skill._last_alerted_error = signal.error
        elif not signal.error:
            skill._last_alerted_error = None
        # 3. act on the decision
        if signal.action in ("LONG", "SHORT") and position is None and not paused:
            pm.open(skill, signal.action, candle, now_ms, weight)
        elif signal.action == "CLOSE" and position is not None:
            pm.close(skill, position, candle.close, candle, now_ms)
        # 4. equity snapshot
        pos_now = db.get_open_position(skill.name)
        unreal = broker.unrealized_pnl(pos_now, candle.close) if pos_now else 0.0
        db.record_equity(skill.name, db.get_balance(skill.name) + unreal, now_ms)


def _drain_commands(db, broker, skills, candle, commands):
    """Execute queued manual commands (e.g. exit a trade) in the DB-owning thread."""
    if commands is None:
        return
    now_ms = int(time.time() * 1000)
    pm = PositionManager(db, broker)
    while True:
        try:
            cmd = commands.get_nowait()
        except Exception:
            break
        if cmd.get("action") == "close":
            sk = next((s for s in skills if s.name == cmd.get("strategy")), None)
            if sk is not None:
                pos = db.get_open_position(sk.name)
                if pos is not None:
                    pm.close(sk, pos, candle.close, candle, now_ms)


def run(cfg=CONFIG, *, fetcher=None, max_ticks=None, sleeper=None, notifier=None,
        should_stop=None, is_paused=None, commands=None):
    sleeper = sleeper or time.sleep
    repo = Repository.open(cfg.db_path)
    broker = Broker(cfg.fee, cfg.slippage)
    # Mechanical skills act once per new candle; AI traders run every cycle on their own
    # wall-clock cadence (so they can poll faster than the candle interval). Both independent.
    mech_skills = build_skills(cfg.enabled_skills, cfg)
    ai_traders = build_ai_traders(cfg)
    skills = mech_skills + ai_traders
    for s in skills:
        repo.ensure_strategy(s.name, cfg.starting_balance)
    last_alert_id = 0
    if notifier is not None:
        last_alert_id = repo.max_trade_id()
    # Risk guard is active only when limits are configured; otherwise None (no overhead).
    guard = None
    if (getattr(cfg, "max_daily_loss", 0) > 0 or getattr(cfg, "max_trade_amount_per_day", 0) > 0
            or not getattr(cfg, "trading_enabled", True)):
        guard = DailyRiskGuard.from_config(cfg)
    ticks = 0
    try:
        while max_ticks is None or ticks < max_ticks:
            if should_stop is not None and should_stop():
                break  # clean shutdown requested (e.g. SIGTERM)
            try:
                candles = get_candles(cfg.pair_candles, cfg.interval, fetcher=fetcher)
            except Exception as exc:  # transient network/API error: skip this tick, keep looping
                print(f"[feed] fetch failed, skipping tick: {exc}")
                candles = []
            if candles:
                repo.save_candles(cfg.pair_candles, cfg.interval, candles, source="live")
                newest = str(candles[-1].time)
                # Manual commands from the UI (e.g. exit a trade) run here, in this thread.
                _drain_commands(repo, broker, skills, candles[-1], commands)
                # Mechanical skills: only on a genuinely new candle (candle-driven).
                if mech_skills and repo.get_state("last_candle_time") != newest:
                    process_tick(repo, broker, mech_skills, candles, cfg, guard, None, is_paused)
                    repo.set_state("last_candle_time", newest)
                # AI traders: every cycle; each one's wall-clock cadence gates actual consults.
                # Pass the notifier so a Claude/CLI error pings Discord (deduped in process_tick).
                if ai_traders:
                    process_tick(repo, broker, ai_traders, candles, cfg, guard, notifier, is_paused)
                ai_names = {t.name for t in ai_traders}
                if notifier is not None:
                    for t in repo.trades_after(last_alert_id):
                        msg = f"{t['side']} {t['size']:.6f} @ {t['price']:.2f} pnl={t['pnl']:.2f}"
                        if t["strategy"] in ai_names:   # show WHY for AI trades
                            why = repo.latest_llm_rationale(t["strategy"])
                            if why:
                                msg += f"\n💡 {why[:280]}"
                        notifier.notify("trade", f"{t['strategy']} {t['action']}", msg)
                        last_alert_id = t["id"]
                # KILL SWITCH: stop the bot immediately when the daily loss limit trips.
                if guard is not None and guard.halted_reason:
                    if notifier is not None:
                        notifier.notify("error", "risk halt", guard.halted_reason)
                    print(f"[risk] halting: {guard.halted_reason}")
                    break
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
        repo.close()


if __name__ == "__main__":
    run()
