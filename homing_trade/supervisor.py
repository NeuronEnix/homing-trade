"""Render OS-level supervisor configs so the daemon restarts on crash AND on reboot.

Two layers of supervision:
  - INNER: `homing_trade.daemon` already supervises the trading engine itself (auto-restart
    with interruptible backoff, status file, clean SIGTERM/SIGINT).
  - OUTER (this module): a macOS launchd agent or a Linux systemd unit that (re)starts the
    daemon PROCESS at boot and if the whole process ever exits. The daemon health-pings
    #comms on start/stop/crash through its notifier, so the OS supervisor needs no extra hook.

`python -m homing_trade.supervisor --kind launchd|systemd [--python … --workdir … --user …]`
prints the rendered config to stdout. See docs/always-on.md for install/uninstall.
"""
import argparse
import os
import sys
from xml.sax.saxutils import escape as _xml_escape

DEFAULT_LABEL = "com.homing-trade.daemon"
DEFAULT_SERVICE = "homing-trade"


def _defaults(python=None, workdir=None):
    """Fill in the current venv interpreter + repo dir when not overridden."""
    return (python or sys.executable), (workdir or os.getcwd())


def render_launchd_plist(*, python=None, workdir=None, label=DEFAULT_LABEL,
                         stdout_log=None, stderr_log=None):
    """A macOS LaunchAgent plist. RunAtLoad + KeepAlive give boot-start + restart-on-exit;
    install into ~/Library/LaunchAgents and `launchctl load` it."""
    python, workdir = _defaults(python, workdir)
    stdout_log = stdout_log or os.path.join(workdir, "data", "daemon.out.log")
    stderr_log = stderr_log or os.path.join(workdir, "data", "daemon.err.log")
    # Escape every interpolated value — a repo path can legitimately contain &, <, > (e.g.
    # ~/Projects/R&D/...), which would otherwise produce a malformed plist.
    label, python, workdir, stdout_log, stderr_log = (
        _xml_escape(label), _xml_escape(python), _xml_escape(workdir),
        _xml_escape(stdout_log), _xml_escape(stderr_log))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>-m</string>
    <string>homing_trade.daemon</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{workdir}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{stdout_log}</string>
  <key>StandardErrorPath</key>
  <string>{stderr_log}</string>
</dict>
</plist>
"""


def render_systemd_unit(*, python=None, workdir=None, service=DEFAULT_SERVICE,
                        user=None, restart_sec=5):
    """A Linux systemd service. Restart=always + WantedBy=multi-user.target give
    restart-on-exit + boot-start once `systemctl enable --now {service}` is run."""
    python, workdir = _defaults(python, workdir)
    user_line = f"User={user}\n" if user else ""
    return f"""[Unit]
Description=homing-trade autonomous paper-trading daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={workdir}
ExecStart={python} -m homing_trade.daemon
{user_line}Restart=always
RestartSec={restart_sec}

[Install]
WantedBy=multi-user.target
"""


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Render an OS supervisor config for the homing-trade daemon.")
    p.add_argument("--kind", choices=["launchd", "systemd"], required=True)
    p.add_argument("--python", default=None, help="interpreter to run (default: current venv)")
    p.add_argument("--workdir", default=None, help="repo working directory (default: cwd)")
    p.add_argument("--user", default=None, help="systemd: run the service as this user")
    args = p.parse_args(argv)
    out = (render_launchd_plist(python=args.python, workdir=args.workdir)
           if args.kind == "launchd"
           else render_systemd_unit(python=args.python, workdir=args.workdir, user=args.user))
    print(out, end="")
    return out


if __name__ == "__main__":
    main()
