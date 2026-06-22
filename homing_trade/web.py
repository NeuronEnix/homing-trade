"""Browser dashboard + control center for the homing-trade bot.

`python -m homing_trade.web` serves a single-page UI on http://localhost:<web_port> and runs
the trading engine in a background thread. The UI gives full visibility (live prices, every
algo + AI brain, positions, logs) and control (start / pause / resume / stop / reset, and
exit any open trade). Stdlib only — no Flask/React.

Run the UI INSTEAD of the bare daemon (both run an engine; don't run both on one DB).
"""
import http.server
import json
import os
import queue
import sqlite3
import threading
import time
import webbrowser

from homing_trade.config import CONFIG, from_env
from homing_trade.repository import Repository
from homing_trade.selfquery import SelfQuery
from homing_trade.engine import run as engine_run
from homing_trade.feed import get_prices
from homing_trade.notify import build_notifier
from homing_trade.proposals import ProposalApplier, ProposalApplyError


class Controller:
    """Owns the engine thread and the start/stop/pause/reset/exit controls."""

    def __init__(self, cfg, runner=None, notifier=None):
        self.cfg = cfg
        self._runner = runner or engine_run
        self._thread = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._commands = queue.Queue()
        self.last_error = None
        self.notifier = notifier or build_notifier(cfg)
        # Per-strategy enable/disable. A disabled strategy keeps existing-position risk
        # management but takes no new decisions (and AI traders skip the consult). Runtime
        # state — resets on restart. The engine reads it live via the is_disabled callback.
        self._disabled = set()

    def status(self):
        if self._thread and self._thread.is_alive():
            return "paused" if self._paused.is_set() else "running"
        return "stopped"

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._paused.clear()
        self.last_error = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self._runner(self.cfg, notifier=self.notifier, should_stop=self._stop.is_set,
                         is_paused=self._paused.is_set, sleeper=lambda s: self._stop.wait(s),
                         commands=self._commands, is_disabled=lambda n: n in self._disabled)
        except Exception as exc:  # surface engine crashes in the UI
            self.last_error = str(exc)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=30)
            self._thread = None

    def pause(self):
        self._paused.set()

    def resume(self):
        self._paused.clear()

    def close_trade(self, strategy):
        if strategy:
            self._commands.put({"action": "close", "strategy": strategy})

    def set_strategy_enabled(self, strategy, enabled):
        """Enable/disable one strategy at runtime (the engine reads this live each tick)."""
        if not strategy:
            return
        if enabled:
            self._disabled.discard(strategy)
        else:
            self._disabled.add(strategy)

    @property
    def disabled(self):
        return set(self._disabled)

    def decide_proposal(self, proposal_id, decision):
        """Approve (and immediately apply) or reject a pending proposal from the UI / #comms.
        Approve is the human gate; apply is the mechanical effect — only the playbook kind
        auto-applies today (param/prompt/strategy_toggle stay approved-but-unapplied until their
        runtime override stores exist). Opens its own short-lived repo connection (WAL lets it
        run alongside the engine thread). Returns a result dict for the caller to surface."""
        if decision not in ("approve", "reject"):
            return {"ok": False, "error": "decision must be approve|reject"}
        repo = Repository.open(self.cfg.db_path)
        try:
            now = int(time.time() * 1000)
            if decision == "reject":
                ok = repo.decide_proposal(proposal_id, "rejected", "human:web", now)
                return {"ok": ok, "status": "rejected" if ok else "not_pending"}
            ok = repo.decide_proposal(proposal_id, "approved", "human:web", now)
            if not ok:
                return {"ok": False, "error": "proposal not pending"}
            try:
                result = ProposalApplier(repo).apply(proposal_id, applied_by="human:web", now_ms=now)
                return {"ok": True, "status": "applied", "result": result}
            except (ProposalApplyError, ValueError) as exc:
                # Approved, but not auto-applied (kind not wired, or a guard tripped). The approval
                # stands; the row stays approved + un-applied for an operator to handle.
                return {"ok": True, "status": "approved", "applied": False, "note": str(exc)}
        except sqlite3.OperationalError as exc:
            # Write contention with the engine thread beyond busy_timeout — report it as a
            # retryable error instead of letting it escape and drop the HTTP connection.
            return {"ok": False, "error": f"db busy, retry: {exc}"}
        finally:
            repo.close()

    def reset(self):
        """Stop and wipe the paper ledger (keeps cached candles)."""
        self.stop()
        repo = Repository.open(self.cfg.db_path)
        try:
            repo.reset_paper_ledger()
        finally:
            repo.close()


def build_state(cfg, controller):
    """Snapshot of everything the UI shows (read-only)."""
    repo = Repository.open(cfg.db_path)
    disabled = controller.disabled
    try:
        sq = SelfQuery(repo, cfg.starting_balance)
        names = repo.strategy_names()
        # Per-strategy performance metrics (win rate / profit factor / drawdown / sharpe /
        # expectancy), already ranked by equity — this IS the leaderboard.
        board = sq.leaderboard(names)
        perf = {p["strategy"]: p for p in board}
        rank = {p["strategy"]: i + 1 for i, p in enumerate(board)}
        # Per-provider cost rollup (Phase 5 #4): tokens + $ per AI strategy from the cost_ledger.
        costs = sq.cost_summary()
        # Latest decided action per strategy (for the "last action" column). recent_decisions
        # is newest-first, so the first row seen per strategy is its most recent.
        last_action = {}
        for d in repo.recent_decisions(200):
            last_action.setdefault(d["strategy"], d["action"])
        strategies = []
        for n in names:
            bal = repo.get_balance(n)
            pos = repo.get_open_position(n)
            eq = repo.latest_equity(n)
            equity = eq if eq is not None else bal
            position = None
            if pos is not None:
                position = {"side": pos.side, "size": round(pos.size, 6),
                            "entry": round(pos.entry_price, 2), "leverage": pos.leverage,
                            "stop": round(pos.stop_price, 2),
                            "unreal": round(equity - bal, 2)}
            m = perf.get(n, {})
            pf = m.get("profit_factor")
            item = {"name": n, "is_ai": n.startswith("llm_"), "rank": rank.get(n),
                    "disabled": n in disabled,
                    "balance": round(bal, 2), "equity": round(equity, 2),
                    "pnl_pct": round((equity - cfg.starting_balance) / cfg.starting_balance * 100, 2),
                    "trades": m.get("trades", 0),
                    "win_rate": round(m.get("win_rate", 0.0), 4),
                    "profit_factor": (round(pf, 2) if pf is not None else None),
                    "max_drawdown": round(m.get("max_drawdown", 0.0), 2),
                    "expectancy": round(m.get("expectancy", 0.0), 2),
                    "sharpe": round(m.get("sharpe", 0.0), 3),
                    "realized_pnl": round(m.get("realized_pnl", 0.0), 2),
                    "last_action": last_action.get(n),
                    # compact equity curve for a sparkline (last 40 snapshots)
                    "equity_curve": [round(e, 2) for e in repo.equity_series(n)[-40:]],
                    "position": position}
            if n.startswith("llm_"):
                c = costs.get(n)
                if c is not None:
                    item["cost"] = {"calls": c["calls"], "total_tokens": c["total_tokens"],
                                    "prompt_tokens": c["prompt_tokens"],
                                    "completion_tokens": c["completion_tokens"],
                                    "usd": round(c["usd"], 6)}
                lr = repo.recent_llm_responses(n, 1)
                if lr:
                    r = lr[0]
                    item["ai"] = {"action": r["action"], "confidence": r["confidence"],
                                  "observation": r["observation"], "prediction": r["prediction"],
                                  "rationale": r["rationale"], "error": r["error"], "ts": r["ts"]}
            strategies.append(item)
        strategies.sort(key=lambda it: it["rank"] if it["rank"] is not None else 1e9)
        trades = repo.recent_trades(25)
        decisions = repo.recent_decisions(40)
        # Brain-log: recent AI responses across all AIs (newest first), trimmed to the
        # replayable fields — what it saw / predicted / why + cadence + any error. The bulky
        # raw envelope is intentionally excluded to keep the snapshot light.
        brain_log = [
            {"strategy": r["strategy"], "ts": r["ts"], "backend": r["backend"],
             "model": r["model"], "action": r["action"], "confidence": r["confidence"],
             "observation": r["observation"], "prediction": r["prediction"],
             "rationale": r["rationale"], "next_check_in_sec": r["next_check_in_sec"],
             "error": r["error"]}
            for r in repo.recent_llm_responses(None, 30)
        ]
        # Per-regime & per-exit-reason attribution over completed trades (read-only over the
        # denormalized trade_outcomes table). No embargo here: the dashboard shows the human
        # all realized outcomes; the as_of embargo is for the learning loop, not display.
        regime_breakdown = sq.regime_performance()
        exit_breakdown = sq.exit_reason_breakdown()
        # External-signal cache freshness (Phase 6 #6): per-source fetched_at / age, so the cache
        # of research feeds (sentiment, derivs, orderbook, price-ref, news) is inspectable.
        from homing_trade.signals.cache import signal_status
        signals = signal_status(repo)
        # Proposal queue: the AI's pending suggestions awaiting human approve/reject, plus the
        # recently-decided ones for feedback. payload is parsed so the UI can show rules/params.
        proposals = []
        for p in repo.recent_proposals(limit=25):
            try:
                payload = json.loads(p["payload_json"])
            except Exception:
                payload = None
            proposals.append({
                "id": p["id"], "strategy": p["strategy"], "kind": p["kind"],
                "rationale": p["rationale"], "status": p["status"],
                "created_ts": p["created_ts"], "decided_by": p.get("decided_by"),
                "applied_ts": p.get("applied_ts"), "applied_result": p.get("applied_result"),
                "payload": payload})
        # Continuous backtest results (Phase 7 #7): the latest walk-forward OOS + trusted
        # (post-cutoff) metrics per strategy, so the dashboard shows honest out-of-sample evidence.
        backtests = repo.latest_backtest_per_strategy()
        return {
            "status": controller.status(),
            "last_error": controller.last_error,
            "starting_balance": cfg.starting_balance,
            "config": {"interval": cfg.interval, "leverage_max": cfg.leverage_max,
                       "kill_switch": cfg.max_daily_loss, "pair": cfg.pair_candles},
            "strategies": strategies, "trades": trades, "decisions": decisions,
            "brain_log": brain_log,
            "regime_breakdown": regime_breakdown, "exit_breakdown": exit_breakdown,
            "proposals": proposals, "signals": signals, "backtests": backtests,
        }
    finally:
        repo.close()


class _Handler(http.server.BaseHTTPRequestHandler):
    cfg = None
    controller = None
    _price_cache = {"ts": 0.0, "data": {}}

    def log_message(self, *args):
        pass  # keep the console quiet

    def _send(self, body, code=200, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _prices(self):
        c = _Handler._price_cache
        if time.time() - c["ts"] > 5:  # cache so polling doesn't hammer CoinDCX
            try:
                c["data"] = get_prices(self.cfg.price_symbols)
            except Exception as exc:
                c["data"] = {"error": str(exc)}
            c["ts"] = time.time()
        return c["data"]

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self._send(DASHBOARD_HTML, ctype="text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._send(build_state(self.cfg, self.controller))
        elif self.path == "/api/prices":
            self._send(self._prices())
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except Exception:
            body = {}
        try:
            self._dispatch_post(body)
        except Exception as exc:   # backstop: always send SOME response, never drop the socket
            self._send({"ok": False, "error": str(exc)}, 500)

    def _dispatch_post(self, body):
        if self.path == "/api/control":
            action = body.get("action")
            ctrl = self.controller
            fn = {"start": ctrl.start, "stop": ctrl.stop, "pause": ctrl.pause,
                  "resume": ctrl.resume, "reset": ctrl.reset}.get(action)
            if fn is None:
                return self._send({"error": "unknown action"}, 400)
            fn()
            self._send({"status": ctrl.status()})
        elif self.path == "/api/close":
            self.controller.close_trade(body.get("strategy"))
            self._send({"ok": True})
        elif self.path == "/api/toggle":
            strategy = body.get("strategy")
            if not strategy:
                return self._send({"error": "strategy required"}, 400)
            self.controller.set_strategy_enabled(strategy, bool(body.get("enabled")))
            self._send({"ok": True, "disabled": sorted(self.controller.disabled)})
        elif self.path == "/api/proposal":
            # bool is an int subclass — reject it explicitly so {"id": true} can't target id 1.
            pid_raw = body.get("id")
            if isinstance(pid_raw, bool):
                return self._send({"error": "valid id required"}, 400)
            try:
                pid = int(pid_raw)
            except (TypeError, ValueError):
                return self._send({"error": "valid id required"}, 400)
            decision = body.get("decision")
            if decision not in ("approve", "reject"):
                return self._send({"error": "decision must be approve|reject"}, 400)
            self._send(self.controller.decide_proposal(pid, decision))
        else:
            self._send({"error": "not found"}, 404)


def make_server(cfg, controller):
    _Handler.cfg = cfg
    _Handler.controller = controller
    return http.server.ThreadingHTTPServer(("127.0.0.1", cfg.web_port), _Handler)


def main(cfg=None, *, open_browser=True):
    cfg = cfg or from_env()
    controller = Controller(cfg)
    controller.start()  # bot is live the moment the UI opens
    server = make_server(cfg, controller)
    url = f"http://localhost:{server.server_address[1]}"
    print(f"homing-trade UI → {url}  (Ctrl-C to stop)")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop()
        server.shutdown()


_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "web_assets")


def _load_dashboard_html():
    """Load the single-page dashboard markup shipped alongside this module."""
    with open(os.path.join(_ASSETS_DIR, "dashboard.html"), encoding="utf-8") as f:
        return f.read()


DASHBOARD_HTML = _load_dashboard_html()


if __name__ == "__main__":
    main()
