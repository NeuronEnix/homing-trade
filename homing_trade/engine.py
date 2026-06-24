# homing_trade/engine.py
import os
import time
import uuid
from homing_trade.allocator import compute_allocations, recent_performance
from homing_trade.arming import select_broker
from homing_trade.config import CONFIG, effective_leverage
from homing_trade.error_boundary import ErrorBoundary
from homing_trade.risk import DailyRiskGuard
from homing_trade.repository import Repository
from homing_trade.feed import get_candles
from homing_trade.position_manager import PositionManager
from homing_trade.skills.indicators import classify_regime
from homing_trade.regime_filter import regime_weight, committee_threshold_scale
from homing_trade.skills.ma_trend import MaTrend
from homing_trade.skills.rsi_revert import RsiRevert
from homing_trade.skills.grid import Grid
from homing_trade.skills.rl_qlearn import RLQLearn
from homing_trade.skills.committee import Committee, build_agents
from homing_trade.skills.macd import MacdCross
from homing_trade.skills.bollinger import BollingerRevert
from homing_trade.skills.donchian import DonchianBreakout
from homing_trade.skills.supertrend import Supertrend
from homing_trade.skills.zscore_revert import ZScoreRevert
from homing_trade.skills.vol_breakout import VolumeBreakout
from homing_trade.skills.ttm_squeeze import TtmSqueeze
from homing_trade.ai_traders import build_ai_traders
from homing_trade.reflect_runner import ReflectionRunner, build_reflect_fn
from homing_trade.research import ResearchRunner, build_research_fn

_SKILL_FACTORY = {
    "ma_trend": MaTrend,
    "rsi_revert": RsiRevert,
    "grid": Grid,
    "macd": MacdCross,
    "bollinger": BollingerRevert,
    "donchian": DonchianBreakout,
    "supertrend": Supertrend,          # Phase 7 #2 candidates (registered, not yet in enabled_skills)
    "zscore_revert": ZScoreRevert,
    "vol_breakout": VolumeBreakout,
    "ttm_squeeze": TtmSqueeze,
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
    opened, _reason = PositionManager(db, broker, cfg, guard).open(skill, side, candle, now_ms, weight)
    return opened


def process_tick(db, broker, skills, candles, cfg, guard=None, notifier=None, is_paused=None,
                 is_disabled=None, error_boundary=None):
    candle = candles[-1]
    now_ms = int(time.time() * 1000)  # wall-clock event time for the audit trail
    paused = bool(is_paused and is_paused())  # when paused: manage/close existing, open nothing new
    pm = PositionManager(db, broker, cfg, guard)
    # Tag this tick's market context once; every decision in the loop is stamped with it.
    reg = classify_regime(candles)
    db.record_regime(cfg.pair_candles, cfg.interval, candle.time,
                     reg["regime"], reg["adx"], reg["ema_slope"], reg["realized_vol"])
    if getattr(cfg, "allocator_enabled", False):
        perf = {s.name: recent_performance(db, s.name, cfg.allocator_lookback) for s in skills}
        weights = compute_allocations(perf)
    else:
        weights = {}
    regime_on = getattr(cfg, "regime_filter_enabled", False)
    for skill in skills:
        weight = weights.get(skill.name, 1.0)
        # Regime gate (Phase 7 #3): scale the allocator weight by the strategy's style fit to the
        # current regime, and tell the committee to demand stronger consensus when not trending.
        if regime_on:
            weight *= regime_weight(skill.name, reg["regime"],
                                    unfavored=cfg.regime_unfavored_weight)
            skill._regime_threshold_scale = committee_threshold_scale(
                reg["regime"], non_trending=cfg.regime_committee_threshold_scale)
        elif hasattr(skill, "_regime_threshold_scale"):
            skill._regime_threshold_scale = 1.0   # clear a stale scale if the gate was turned off
        # 1. risk checks on any existing position (stop / liquidation) — runs even when the
        #    strategy is disabled, so a parked position still gets its stop/liquidation safety.
        position = pm.manage_risk(skill, db.get_open_position(skill.name), candle, now_ms)
        # 1b. disabled strategy (manual toggle OR an ErrorBoundary trip — a skill auto-disabled after
        #     repeated crashes): keep its existing position risk-managed, but take no new decision —
        #     skips the consult entirely (no AI cost) and opens nothing new. Its open position can
        #     still be exited via the manual close command (_drain_commands).
        eb_tripped = error_boundary is not None and error_boundary.is_tripped(skill.name)
        if eb_tripped or (is_disabled and is_disabled(skill.name)):
            pos_now = db.get_open_position(skill.name)
            unreal = broker.unrealized_pnl(pos_now, candle.close) if pos_now else 0.0
            db.record_equity(skill.name, db.get_balance(skill.name) + unreal, now_ms)
            continue
        # 2. strategy decision — isolated by the ErrorBoundary (when supplied) so a single skill that
        #    RAISES can't abort the tick for the whole roster. A raise is counted; after N consecutive
        #    crashes the skill trips (auto-disabled) with a recorded risk_event + one alert.
        if error_boundary is not None:
            try:
                signal = skill.on_candle(candles, position)
            except Exception as e:
                newly_tripped = error_boundary.record_failure(skill.name, e)
                if newly_tripped:
                    # Fires exactly once by construction (record_failure returns True only on the
                    # trip), so this alert is intentionally one-shot and outside the soft-error dedup.
                    db.record_risk_event(now_ms, skill.name, "skill_disabled",
                                         f"auto-disabled after {error_boundary.threshold} consecutive "
                                         f"errors; last: {e}")
                    if notifier is not None:
                        notifier.notify("error", f"{skill.name} auto-disabled", str(e)[:400])
                pos_now = db.get_open_position(skill.name)   # equity continuity; no decision this tick
                unreal = broker.unrealized_pnl(pos_now, candle.close) if pos_now else 0.0
                db.record_equity(skill.name, db.get_balance(skill.name) + unreal, now_ms)
                continue
            error_boundary.record_success(skill.name)        # a clean run clears the failure streak
        else:
            signal = skill.on_candle(candles, position)
        decision_id = uuid.uuid4().hex
        m = signal.meta or {}     # AI provenance (reasoning + prompt/playbook version + hash)
        # 2b. persist the full AI response + reasoning (only on a real consult or an error)
        if signal.raw or signal.error:
            db.record_llm_response(skill.name, now_ms, getattr(skill, "backend", ""),
                                   getattr(skill, "model", ""), signal.action, signal.confidence,
                                   m.get("observation", ""), m.get("prediction", ""),
                                   m.get("rationale", ""), signal.raw or "", signal.error or "",
                                   next_check_in_sec=m.get("next_check_in_sec"),
                                   requested_charts=m.get("requested_charts"),
                                   prompt_version=m.get("prompt_version"),
                                   prompt_hash=m.get("prompt_hash"))
        # 2b'. per-provider cost accounting (Phase 5 #4): one cost_ledger row per real consult that
        #      reported usage. Skip HOLD-waiting (no raw) and error paths (no usage).
        if signal.raw and not signal.error:
            u = m.get("usage") or {}
            if any(u.get(k) is not None for k in ("prompt_tokens", "completion_tokens", "usd")):
                db.record_cost(skill.name, now_ms, getattr(skill, "model", ""),
                               getattr(skill, "backend", ""), u.get("prompt_tokens"),
                               u.get("completion_tokens"), u.get("usd"))
        # 2c. alert on an AI error (deduped so a persistent failure doesn't spam Discord)
        if signal.error and notifier is not None:
            if getattr(skill, "_last_alerted_error", None) != signal.error:
                notifier.notify("error", f"{skill.name} — Claude error", signal.error[:400])
                skill._last_alerted_error = signal.error
        elif not signal.error:
            skill._last_alerted_error = None
        # 3. act on the decision, recording what was actually taken (and why, if blocked)
        taken_action, rejection = "HOLD", None
        if signal.action in ("LONG", "SHORT"):
            if position is None:
                if paused:
                    taken_action, rejection = "PAUSED", "paused: new entries disabled"
                else:
                    opened, reason = pm.open(skill, signal.action, candle, now_ms, weight,
                                             decision_id=decision_id, regime_at_entry=reg["regime"])
                    taken_action, rejection = (signal.action, None) if opened else ("BLOCKED", reason)
            elif position.side != signal.action:
                # Reversal: the strategy's own thesis flipped to the opposite side. ALWAYS close the
                # current position (else a trend strategy with no CLOSE signal rides it to the stop).
                # Then, if flips are enabled and entries aren't paused, open the opposite side so the
                # strategy can actually trade the new direction (e.g. short a confirmed downtrend).
                pm.close(skill, position, candle.close, candle, now_ms, exit_reason="reversal")
                if cfg.reversal_flip_enabled and not paused:
                    opened, reason = pm.open(skill, signal.action, candle, now_ms, weight,
                                             decision_id=decision_id, regime_at_entry=reg["regime"])
                    taken_action, rejection = (signal.action, None) if opened else ("CLOSE", reason)
                else:
                    taken_action = "CLOSE"
            else:
                # Same-side signal while already in that position — nothing to do (already aligned).
                taken_action, rejection = "HOLD", "already in position (same side)"
        elif signal.action == "CLOSE" and position is not None:
            pm.close(skill, position, candle.close, candle, now_ms, exit_reason="signal")
            taken_action = "CLOSE"
        # 3b. log the decision with full provenance (intended vs taken, why blocked)
        db.log_decision(skill.name, now_ms, candle.time, signal.action, signal.confidence,
                        signal.reason, signal.indicators, decision_id=decision_id,
                        intended_action=signal.action, taken_action=taken_action,
                        rejection_rationale=rejection, regime=reg["regime"],
                        realized_vol=reg["realized_vol"],
                        prompt_version=m.get("prompt_version"),
                        playbook_version=m.get("playbook_version"))
        # 4. equity snapshot
        pos_now = db.get_open_position(skill.name)
        unreal = broker.unrealized_pnl(pos_now, candle.close) if pos_now else 0.0
        db.record_equity(skill.name, db.get_balance(skill.name) + unreal, now_ms)
    # Refresh the denormalized outcome table (open->close joins + MAE/MFE from the candle path)
    # so the UI's per-regime/per-exit attribution and the reflection layer read fresh data.
    # Runs once per tick (candle for mech skills, poll cadence for AI traders) + idempotent;
    # cost scales with completed-trade count, not history. A no-op on ledgers without a
    # trade_outcomes table (the in-memory backtest ledger), so backtests stay linear.
    db.rebuild_trade_outcomes(cfg.pair_candles, cfg.interval)


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
                    pm.close(sk, pos, candle.close, candle, now_ms, exit_reason="manual")


class SkillRunner:
    """Builds the strategy roster and runs it each tick.

    Owns what used to be inline in engine.run: building the mechanical skills + AI traders,
    ensuring each has a wallet, and per-tick execution — mechanical skills act once per NEW
    candle (candle-driven); AI traders run every cycle on their own wall-clock cadence. Also
    emits trade alerts (with the AI's rationale) through the notifier. engine.run is left a
    thin fetch/sleep loop that drives this.
    """

    def __init__(self, cfg, ledger, broker, guard=None, notifier=None):
        self.cfg = cfg
        self.ledger = ledger
        self.broker = broker
        self.guard = guard
        self.notifier = notifier
        # Crash isolation for the always-on loop: one breaker shared across the roster, so a skill
        # that raises is counted and (after N consecutive crashes) auto-disabled — never aborting
        # the tick. Backtests don't pass a breaker, so their behavior is unchanged.
        self.error_boundary = ErrorBoundary(getattr(cfg, "error_boundary_threshold", 3))
        self.mech_skills = build_skills(cfg.enabled_skills, cfg)
        self.ai_traders = build_ai_traders(cfg)
        self.skills = self.mech_skills + self.ai_traders
        for s in self.skills:
            ledger.ensure_strategy(s.name, cfg.starting_balance)
        # Give each AI trader a live read of ITS OWN current playbook (the human-approved,
        # learn->correct output). build_ai_traders has no ledger reference, so wire it here.
        # Guarded: backtest ledgers without a playbook store simply don't get a provider.
        if hasattr(ledger, "latest_playbook"):
            for t in self.ai_traders:
                if hasattr(t, "set_playbook_provider"):
                    t.set_playbook_provider(lambda name=t.name: ledger.latest_playbook(name))
        # Inject external sentiment (Fear & Greed) into every AI brain when enabled. The provider is
        # GLOBAL (one reading for the whole market), cache-aware, and degrades to None on any fetch
        # failure — so it never blocks a consult. Off by default / on backtest ledgers without a cache.
        if getattr(cfg, "fng_enabled", False) and hasattr(ledger, "get_signal"):
            from homing_trade.signals.fng import get_fng
            for t in self.ai_traders:
                if hasattr(t, "set_fng_provider"):
                    t.set_fng_provider(lambda: get_fng(ledger))
        # Derivatives (perp funding + open interest) — per-trader by its own instrument's Binance
        # symbol (p=t.pair binds per-iteration to avoid late-binding); cache-aware, degrade-safe.
        if getattr(cfg, "derivs_enabled", False) and hasattr(ledger, "get_signal"):
            from homing_trade.signals.derivs import get_derivs, binance_symbol
            for t in self.ai_traders:
                if hasattr(t, "add_context_provider"):
                    t.add_context_provider(
                        "derivatives",
                        lambda p=getattr(t, "pair", cfg.pair_candles): get_derivs(ledger, binance_symbol(p)))
        # CoinDCX microstructure — the ACTUAL traded instrument (source of truth), per-trader by its
        # own pair (p=t.pair bound per-iteration). Cache-aware, degrade-safe.
        if getattr(cfg, "coindcx_signal_enabled", False) and hasattr(ledger, "get_signal"):
            from homing_trade.signals.coindcx import get_coindcx
            for t in self.ai_traders:
                if hasattr(t, "add_context_provider"):
                    t.add_context_provider(
                        "coindcx",
                        lambda p=getattr(t, "pair", cfg.pair_candles): get_coindcx(ledger, p))
        # Independent reference price (CoinGecko) to sanity-check the venue — GLOBAL (one call covers
        # all assets), cache-aware, degrade-safe. Keyless public tier works; the Demo key just lifts
        # the rate limit. Resolved from the gitignored env var; never logged.
        if getattr(cfg, "price_ref_enabled", False) and hasattr(ledger, "get_signal"):
            from homing_trade.signals.price_ref import get_price_ref, resolve_key
            _pr_key = resolve_key(getattr(cfg, "coingecko_key_env", ""))
            for t in self.ai_traders:
                if hasattr(t, "add_context_provider"):
                    t.add_context_provider("price_ref", lambda k=_pr_key: get_price_ref(ledger, api_key=k))
        # Crypto news headlines (free RSS) — GLOBAL macro/event context, cache-aware, degrade-safe.
        if getattr(cfg, "news_enabled", False) and hasattr(ledger, "get_signal"):
            from homing_trade.signals.news import get_news
            for t in self.ai_traders:
                if hasattr(t, "add_context_provider"):
                    t.add_context_provider("news", lambda: get_news(ledger))
        self._ai_names = {t.name for t in self.ai_traders}
        # Only alert on trades from now on (skip everything already in the ledger).
        self.last_alert_id = ledger.max_trade_id() if notifier is not None else 0
        # The PERIODIC reflection loop (learn->correct). Gated default-OFF; when enabled it
        # retrospects over completed trades on a slow wall-clock cadence and FILES human-gated
        # proposals (it applies nothing). Decoupled from the candle loop, like the AI poll.
        self.reflection = ReflectionRunner(
            ledger, build_reflect_fn(cfg),
            poll_sec=getattr(cfg, "reflection_poll_sec", 3600),
            min_trades=getattr(cfg, "reflection_min_trades", 5),
            starting_balance=cfg.starting_balance,
            # label the reflection with the model actually invoked (matches build_reflect_fn),
            # not a generic tag, so the audit trail names the real model.
            model=(getattr(cfg, "reflection_model", "") or cfg.llm_model or "reflection"))
        # Candidate-strategy intake (Phase 6 #7). Gated default-OFF; when enabled it scans on a slow
        # cadence and FILES human-gated strategy_toggle proposals (never auto-enables anything).
        self.research = ResearchRunner(
            ledger, build_research_fn(cfg),
            poll_sec=getattr(cfg, "research_poll_sec", 86400),
            max_candidates=getattr(cfg, "research_max_candidates", 3),
            model=(getattr(cfg, "research_model", "") or cfg.llm_model or "research"))
        # Continuous walk-forward backtest job (Phase 7 #7). Gated default-OFF; self-paced (daily).
        # When enabled it records OOS + trusted (post-cutoff) results to SQLite for the dashboard and
        # for the candidates' promotion track record. Pulls only stored candles (no network).
        # Lazy import: backtest_job -> walkforward -> backtest -> engine would otherwise cycle.
        from homing_trade.backtest_job import BacktestRunner
        self.backtest_job = BacktestRunner(ledger, cfg)
        # Discord inbound approvals (Phase 3 #8): poll #comms for approve/reject replies and drive
        # the same human-approval gate the web UI uses. Self-gated (no-op unless a bot token is set)
        # and never raises into the trading loop.
        from homing_trade.comms_approvals import CommsApprovalRunner
        self.approvals = CommsApprovalRunner(ledger, cfg)
        # Phase 11 #1: the #paper-trade narration feed. Narrate-only + default-OFF + degrade-safe
        # (no-op unless paper_feed_enabled AND a webhook is set), so constructing it is free and it
        # never disturbs the trading loop. Each new trade is narrated in _emit_trade_alerts.
        from homing_trade.trade_feed import TradeFeed
        self.trade_feed = TradeFeed(cfg)
        # track the running regime so the feed can flag a regime flip (NOTABLE).
        self._last_regime = None
        # If ONLY the feed is active (no legacy notifier), still skip pre-existing trades on the
        # first tick so we don't narrate the whole ledger history.
        if notifier is None and self.trade_feed.enabled:
            self.last_alert_id = ledger.max_trade_id()

    def run_tick(self, candles, *, is_paused=None, commands=None, is_disabled=None):
        candle = candles[-1]
        newest = str(candle.time)
        # Manual UI commands (e.g. exit a trade) run here, in the DB-owning thread.
        _drain_commands(self.ledger, self.broker, self.skills, candle, commands)
        # Mechanical skills: only on a genuinely new candle (candle-driven).
        if self.mech_skills and self.ledger.get_state("last_candle_time") != newest:
            process_tick(self.ledger, self.broker, self.mech_skills, candles,
                         self.cfg, self.guard, None, is_paused, is_disabled, self.error_boundary)
            self.ledger.set_state("last_candle_time", newest)
        # AI traders: every cycle; each one's wall-clock cadence gates actual consults.
        # The notifier lets a Claude/CLI error ping Discord (deduped inside process_tick).
        if self.ai_traders:
            process_tick(self.ledger, self.broker, self.ai_traders, candles,
                         self.cfg, self.guard, self.notifier, is_paused, is_disabled,
                         self.error_boundary)
        self._emit_trade_alerts()
        # Periodic reflection on its own slow cadence (no-op unless reflection_enabled). Self-
        # gated, so calling it every tick is cheap; it consults the model at most every poll_sec.
        # Scoped to the AI traders: they are the only strategies that consume a playbook (the
        # mechanical skills are deterministic), so reflecting over them avoids filing inert
        # playbook proposals for strategies that could never act on one.
        self.reflection.run(sorted(self._ai_names))
        # Candidate-strategy research on its own (daily) cadence — no-op unless research_enabled.
        # Self-gated + isolated; files only human-gated strategy_toggle proposals, never enables.
        self.research.run(sorted(s.name for s in self.skills))
        # Continuous backtest job on its own (daily) cadence — no-op unless continuous_backtest_enabled.
        self.backtest_job.run()
        # Discord inbound approvals on their own (fast) cadence — no-op unless inbound is configured.
        self.approvals.run()

    def _emit_trade_alerts(self):
        if self.notifier is None and not self.trade_feed.enabled:
            return
        for t in self.ledger.trades_after(self.last_alert_id):
            why = self.ledger.latest_llm_rationale(t["strategy"]) if t["strategy"] in self._ai_names else ""
            # Legacy Discord trade alert (unchanged behaviour).
            if self.notifier is not None:
                msg = f"{t['side']} {t['size']:.6f} @ {t['price']:.2f} pnl={t['pnl']:.2f}"
                if why:
                    msg += f"\n💡 {why[:280]}"
                self.notifier.notify("trade", f"{t['strategy']} {t['action']}", msg)
            # Phase 11 #1: narrate to the #paper-trade feed (no-op when disabled; never raises).
            if self.trade_feed.enabled:
                self._narrate_trade(t, why)
            self.last_alert_id = t["id"]

    def _narrate_trade(self, t, why):
        """Build the Phase-11 message contract for one trade and narrate it. Best-effort context
        from the ledger (equity + regime flip + exit reason); the feed's escalation level is
        informational on the paper feed (it never gates)."""
        regime = t.get("regime_at_entry")
        flip = bool(self._last_regime and regime and regime != self._last_regime)
        if regime:
            self._last_regime = regime
        price, size = t.get("price") or 0.0, t.get("size") or 0.0
        action = {
            "kind": "exit" if t["action"] == "CLOSE" else "entry",
            "strategy": t["strategy"], "symbol": self.cfg.pair_candles,
            "side": t.get("side"), "size": size, "price": price,
            "pnl": t.get("pnl"), "notional": abs(size * price),
            "leverage": effective_leverage(self.cfg),
            "exit_reason": t.get("exit_reason"), "decision_id": t.get("decision_id"),
        }
        ctx = {"equity": self.ledger.get_balance(t["strategy"]), "regime_flip": flip}
        self.trade_feed.narrate(action, why, ctx)

    def save(self):
        """Persist any stateful skills (e.g. the RL Q-table) on shutdown."""
        for s in self.skills:
            if hasattr(s, "save"):
                try:
                    s.save()
                except Exception:
                    pass


def _make_guard(cfg):
    """A DailyRiskGuard only when limits are configured (or trading is disabled); else None."""
    if (getattr(cfg, "max_daily_loss", 0) > 0 or getattr(cfg, "max_trade_amount_per_day", 0) > 0
            or not getattr(cfg, "trading_enabled", True)):
        return DailyRiskGuard.from_config(cfg)
    return None


def run(cfg=CONFIG, *, fetcher=None, max_ticks=None, sleeper=None, notifier=None,
        should_stop=None, is_paused=None, commands=None, is_disabled=None):
    sleeper = sleeper or time.sleep
    repo = Repository.open(cfg.db_path)
    # The arming gate picks the broker: PAPER by default; any live mode fails safe until the live
    # execution layer is integrated (Phase 10). So enabling the flags can never silently trade.
    broker, _mode = select_broker(cfg)
    guard = _make_guard(cfg)
    runner = SkillRunner(cfg, repo, broker, guard=guard, notifier=notifier)
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
                runner.run_tick(candles, is_paused=is_paused, commands=commands,
                                is_disabled=is_disabled)
                # KILL SWITCH: stop the bot immediately when the daily loss limit trips.
                if guard is not None and guard.halted_reason:
                    repo.record_risk_event(int(time.time() * 1000), None, "halt", guard.halted_reason)
                    if notifier is not None:
                        notifier.notify("error", "risk halt", guard.halted_reason)
                    print(f"[risk] halting: {guard.halted_reason}")
                    break
            ticks += 1
            if max_ticks is None or ticks < max_ticks:
                sleeper(cfg.poll_seconds)
    finally:
        runner.save()
        repo.close()


if __name__ == "__main__":
    run()
