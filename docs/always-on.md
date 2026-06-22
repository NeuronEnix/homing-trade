# Always-on: running the daemon under an OS supervisor

The bot has **two layers of supervision**:

1. **Inner** — `homing_trade.daemon` supervises the trading *engine*: it auto-restarts the
   engine on crash with an interruptible backoff, writes `data/daemon_status.json`, and shuts
   down cleanly on SIGINT/SIGTERM. When `alert_mode=discord` is configured (with a webhook
   URL), it health-pings `#comms` on **start / stop / crash** through its notifier; otherwise
   those events go to the console.
2. **Outer** — an **OS supervisor** (macOS launchd or Linux systemd) (re)starts the daemon
   *process* itself at **boot** and if the whole process ever exits. That's what this doc sets up.

You only need the outer layer for true always-on (survive reboot / a killed process). Render
the config for your platform with `homing_trade.supervisor`, which fills in your current venv
interpreter and repo path automatically.

> The repo is paper-money by default; `LiveBroker` stays `dry_run`. Running always-on does
> **not** arm live trading — that's a separate explicit gate (Phase 10).

---

## macOS (launchd LaunchAgent)

```sh
cd /path/to/homing-trade
# render with the current venv python + this repo dir baked in
.venv/bin/python -m homing_trade.supervisor --kind launchd > ~/Library/LaunchAgents/com.homing-trade.daemon.plist

launchctl load  ~/Library/LaunchAgents/com.homing-trade.daemon.plist   # start now + at every login
launchctl list | grep homing-trade                                      # confirm it's loaded
```

`RunAtLoad` starts it immediately and at login; `KeepAlive` restarts it if it exits. Logs go
to `data/daemon.out.log` / `data/daemon.err.log` in the repo.

Stop / uninstall:

```sh
launchctl unload ~/Library/LaunchAgents/com.homing-trade.daemon.plist
rm ~/Library/LaunchAgents/com.homing-trade.daemon.plist
```

(A LaunchAgent runs at login. For a headless machine that must run before any user logs in,
install it as a LaunchDaemon under `/Library/LaunchDaemons` instead — same plist, run
`launchctl load` as root.)

---

## Linux (systemd)

```sh
cd /path/to/homing-trade
.venv/bin/python -m homing_trade.supervisor --kind systemd --user "$USER" | sudo tee /etc/systemd/system/homing-trade.service

sudo systemctl daemon-reload
sudo systemctl enable --now homing-trade        # start now + on every boot
systemctl status homing-trade                   # confirm it's running
journalctl -u homing-trade -f                   # follow logs
```

`Restart=always` + `RestartSec=5` restart the process if it exits; `WantedBy=multi-user.target`
starts it on boot. Omit `--user` to run as root (not recommended).

Stop / uninstall:

```sh
sudo systemctl disable --now homing-trade
sudo rm /etc/systemd/system/homing-trade.service
sudo systemctl daemon-reload
```

---

## Verifying it's alive

- **Status file:** `cat data/daemon_status.json` → `{"state": "running"|"restarting"|"stopped", "restarts": N, "last_error": …, "ts": …}`.
- **Discord:** with `alert_mode=discord` configured, you'll get a `#comms` ping on start, on each crash+restart, and on stop.
- **Dashboard:** `python -m homing_trade.web` shows live state (run the UI **or** the daemon on a
  given DB, not both — they each drive an engine).

## Customizing

`--python` / `--workdir` override the interpreter and repo dir; `--user` (systemd) sets the
service user. Daemon behavior (restart backoff, status path, alert channel, risk limits) is
configured via `.env` / `HT_*` env vars — see `config.from_env`.
