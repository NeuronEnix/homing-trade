# Phase 4 — Automation (daemon, alerts, guarded live trading) — Design Spec

- **Date:** 2026-06-21
- **Status:** Approved (design); implementation pending
- **Depends on:** Phases 1–3 — merged to `main`
- **Owner:** devansh@jum.bz

## 1. Goal

Make the bot run unattended and tell you what it's doing, and provide a **complete but
strictly-guarded** path to live trading that the user — never the bot — enables.

Three pieces:
1. **Daemon / supervisor** — keeps the engine running unattended: graceful shutdown,
   heartbeat/status file, restart-on-crash, lifecycle alerts.
2. **Alerts** — pluggable notifications (console / file / optional webhook) on trades and
   daemon lifecycle events.
3. **Live-trading adapter** — a CoinDCX authenticated order adapter (HMAC-signed) that is
   **dry-run by default and never auto-enabled**. The bot will not place a real-money order
   on its own; that requires the user to set `live_enabled=True`, set `live_dry_run=False`,
   and supply their own API keys via environment variables.

## 2. Hard safety rules (non-negotiable)

- **No real orders by default.** `LiveBroker` defaults to `dry_run=True`; in dry-run it NEVER
  makes a network call — it logs the intended order and returns a simulated ack.
- **The automated paper engine never routes to `LiveBroker`** unless the user explicitly
  builds a live runner with `live_enabled=True`. The default daemon runs the *paper* engine.
- **No keys in code or git.** API key/secret are read from environment variables
  (`COINDCX_API_KEY`, `COINDCX_API_SECRET`); absent keys → live path refuses and stays paper.
- All existing tests stay green; new code is additive and default-off.

## 3. Alerts (`notify.py`)

- `Notifier` ABC: `notify(self, level: str, title: str, message: str) -> None` (level ∈
  `"info"|"trade"|"warn"|"error"`).
- `ConsoleNotifier` (default) — prints a formatted line.
- `FileNotifier(path)` — appends a timestamped line to a log file.
- `NullNotifier` — no-op (used by tests / when alerts disabled).
- `WebhookNotifier(url, poster=None)` — POSTs a JSON `{level,title,message}` to `url` via an
  injectable `poster(url, json)` (defaults to a `requests`-based poster). For Slack/Telegram/
  Discord incoming webhooks. Any post error is swallowed (alerts must never crash the bot).
- `build_notifier(cfg) -> Notifier` factory keyed on `cfg.alert_mode`
  (`"console"` default | `"file"` | `"webhook"` | `"null"`).

## 4. Per-trade alerts (engine hook, additive)

- `Database.trades_after(last_id) -> list[dict]` — returns trades with `id > last_id`
  (oldest-first), each a dict incl. `id, strategy, side, action, price, size, pnl`.
- `engine.run(cfg, *, fetcher=None, max_ticks=None, sleeper=None, notifier=None)` — when a
  `notifier` is provided, after each processed tick it fetches new trades via `trades_after`
  and emits one `notify("trade", ...)` per new trade, tracking the last-alerted id. Default
  `notifier=None` → behaviour identical to Phase 1–3 (no change to `process_tick` or the
  sizing path). This is the ONLY change to `engine.run`.

## 5. Daemon / supervisor (`daemon.py`)

- `run_daemon(cfg, *, notifier=None, status_path="data/daemon_status.json", run_engine=None, max_restarts=None, sleeper=None)`:
  - Installs SIGINT/SIGTERM handlers that request a graceful stop.
  - Emits `notify("info", "daemon", "started")`; on exit `"stopped"`.
  - Calls `run_engine` (defaults to `engine.run`) inside a try/except; on an unexpected
    exception emits `notify("error", ...)`, writes the error to the status file, and
    **restarts** (up to `max_restarts`, unbounded if None) after a short backoff — so a
    transient failure doesn't kill the bot.
  - Writes a heartbeat/status JSON (`{state, restarts, last_error, ts}`) to `status_path` so
    you can check on it.
  - `run_engine`/`sleeper` are injectable so the daemon is unit-testable with no real loop:
    a fake `run_engine` that returns immediately (or raises once) exercises start/restart/stop.
- CLI: `python -m algotrading.daemon` runs the paper engine forever with the configured
  notifier and a heartbeat file. **Paper only** — the CLI never constructs a live runner.

## 6. Live-trading adapter (`live_broker.py`) — guarded, dry-run default

- `sign(secret: str, body: dict) -> str` — `hmac.new(secret, json.dumps(body), sha256).hexdigest()`
  over the canonical JSON body (CoinDCX's auth scheme). Pure + deterministic → unit-tested
  against a known vector.
- `build_order_payload(market, side, order_type, quantity, price, now_ms) -> dict` — the
  CoinDCX order body (`{market, side, order_type, price_per_unit, total_quantity, timestamp}`).
- `LiveBroker(api_key=None, api_secret=None, dry_run=True, base_url="https://api.coindcx.com", poster=None)`:
  - `place_order(market, side, order_type, quantity, price, now_ms) -> dict`:
    - **If `dry_run` is True (default): build the payload, log it, and return
      `{"status":"dry_run","payload":...}` — NO network call.**
    - If `dry_run` is False: require `api_key`/`api_secret` (raise a clear error if missing),
      sign the body, and POST to `/exchange/v1/orders/create` via `poster(url, headers, body)`
      (injectable; defaults to `requests`). Returns the parsed response.
  - `from_signal(signal, market, quantity, price, now_ms)` — maps a `Signal` (LONG→buy,
    CLOSE/SHORT→sell) to `place_order`; HOLD → no-op.
- The adapter is standalone and **not imported by the paper engine**. Wiring it into a live
  runner is a documented, deliberate user action; it is out of scope to auto-enable.

## 7. Config additions (`config.py`)
`alert_mode="console"`, `alert_log_path="data/alerts.log"`, `webhook_url=""`,
`live_enabled=False`, `live_dry_run=True`, `coindcx_key_env="COINDCX_API_KEY"`,
`coindcx_secret_env="COINDCX_API_SECRET"`, `daemon_status_path="data/daemon_status.json"`,
`daemon_backoff_seconds=5`.

## 8. Component / file map
```
algotrading/
├── notify.py        # NEW — Notifier ABC + Console/File/Null/Webhook + build_notifier
├── daemon.py        # NEW — supervisor (signals, heartbeat, restart, lifecycle alerts) + CLI
├── live_broker.py   # NEW — guarded CoinDCX order adapter (dry-run default, HMAC signing)
├── db.py            # MODIFY — trades_after(last_id) (additive)
├── engine.py        # MODIFY — run() optional notifier hook (additive, default None)
└── config.py        # MODIFY — Phase-4 fields (additive)
```

## 9. Testing
- Notifiers: Console/File/Null behave; File appends; Webhook posts via injected poster and
  swallows poster errors; `build_notifier` maps modes. No real network.
- `trades_after`: returns only newer trades, oldest-first.
- `engine.run` notifier hook: with a fake notifier + fake fetcher, new trades produce
  `"trade"` notifications; with `notifier=None`, behaviour unchanged (existing tests green).
- Daemon: with an injected `run_engine` that returns immediately → start+stop alerts and a
  status file; one that raises once → a restart then stop (bounded by `max_restarts`); a
  stop request short-circuits the loop. No real signals/loops in tests.
- LiveBroker: `sign` matches a known HMAC vector; `build_order_payload` shape; **dry-run
  places NO call** (injected poster asserts it is never invoked, returns `status="dry_run"`);
  live path with `dry_run=False` and a fake poster signs + posts and returns the response;
  `dry_run=False` with missing keys raises. NO real CoinDCX calls in any test.

## 10. Out of scope
Auto-enabling live trading; multi-exchange; a web dashboard; cloud deployment. The live
adapter is built and tested but never armed by the bot.
