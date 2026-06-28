"""Dev-mode auto-reloader — "nodemon for Python".

Restarts a homing-trade entrypoint whenever a watched source file OR `.env` changes, so you
don't have to bounce it by hand while iterating. A fresh process re-runs `load_dotenv` +
`config.from_env`, so editing `.env` takes effect on the next restart just like editing code.

    python -m homing_trade.devwatch                      # reload `-m homing_trade.web` on change
    python -m homing_trade.devwatch homing_trade.daemon  # reload a different entrypoint
    python -m homing_trade.devwatch --interval 0.5 homing_trade.web --no-browser
    tools/dev.sh                                         # convenience wrapper (venv python)

NOT for production: the daemon/supervisor handle crash + boot restart (see supervisor.py). This
is purely the dev loop. Pure stdlib (no watchdog) — it polls mtimes. It watches `*.py` under the
homing_trade package plus `.env`, and ignores data/, .git, __pycache__, .venv and *.pyc.

Shutdown is graceful: the child runs in its own session, gets SIGINT first (so web.py's
KeyboardInterrupt handler closes the controller + server cleanly), then SIGKILL as a fallback.
"""
import argparse
import os
import signal
import subprocess
import sys
import time

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TARGET = ["homing_trade.web"]
IGNORE_DIRS = {"data", "__pycache__", ".git", ".venv", "node_modules", ".pytest_cache"}
WATCH_SUFFIXES = (".py",)
EXTRA_FILES = (".env",)            # resolved against the cwd the reloader is launched from
GRACE_SECONDS = 8.0                # how long to wait for a graceful SIGINT before SIGKILL


def list_sources(watch_root=PKG_DIR, *, extra_files=EXTRA_FILES,
                 ignore_dirs=IGNORE_DIRS, suffixes=WATCH_SUFFIXES):
    """Every file we watch: source files under watch_root + the resolved extra files (.env).

    Ignored directories are pruned in-place so os.walk never descends into them (cheap on a
    repo with a fat data/ dir). Hidden dirs (dot-prefixed) are skipped too. Returns a sorted
    list of absolute paths that currently exist."""
    found = []
    for dirpath, dirnames, filenames in os.walk(watch_root):
        dirnames[:] = [d for d in dirnames if d not in ignore_dirs and not d.startswith(".")]
        for fn in filenames:
            if fn.endswith(suffixes):
                found.append(os.path.join(dirpath, fn))
    for f in extra_files:
        p = os.path.abspath(f)
        if os.path.isfile(p):
            found.append(p)
    return sorted(set(found))


def snapshot(files):
    """Map each existing file to its mtime (ns). Files that vanish between listing and stat
    are simply dropped — the next diff sees them as removed."""
    snap = {}
    for f in files:
        try:
            snap[f] = os.stat(f).st_mtime_ns
        except OSError:
            pass
    return snap


def changed_files(old, new):
    """Paths whose mtime differs, or that were added/removed between two snapshots. Sorted."""
    return sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))


def build_command(target, *, python=None):
    """Turn a target (['homing_trade.web', '--flag', ...]) into a runnable argv. An empty
    target falls back to the default entrypoint."""
    return [python or sys.executable, "-m", *(target or DEFAULT_TARGET)]


def child_env(base=None):
    """Environment for a reloaded child: a copy of `base` (default os.environ) with HT_NO_BROWSER=1,
    so the web UI never re-opens a browser tab on each restart — the whole point of the dev loop is
    to keep ONE tab open while the server reloads under it."""
    env = dict(os.environ if base is None else base)
    env["HT_NO_BROWSER"] = "1"
    return env


class Reloader:
    """Spawn `command` as a child, watch files, and restart the child on any change."""

    def __init__(self, command, *, watch_root=PKG_DIR, extra_files=EXTRA_FILES,
                 interval=1.0, grace=GRACE_SECONDS, log=print):
        self.command = command
        self.watch_root = watch_root
        self.extra_files = extra_files
        self.interval = interval
        self.grace = grace
        self.log = log
        self.proc = None

    def _scan(self):
        return snapshot(list_sources(self.watch_root, extra_files=self.extra_files))

    def _spawn(self):
        # start_new_session=True puts the child in its own process group, so a Ctrl-C in the
        # terminal hits only the reloader — we then forward a clean stop to the child ourselves.
        self.proc = subprocess.Popen(self.command, start_new_session=True, env=child_env())
        self.log(f"[devwatch] started: {' '.join(self.command)} (pid {self.proc.pid})")

    def _stop_child(self):
        if self.proc is None or self.proc.poll() is not None:
            return
        self.log("[devwatch] stopping child …")
        try:
            self.proc.send_signal(signal.SIGINT)   # graceful: triggers web.py's clean shutdown
        except ProcessLookupError:
            return
        try:
            self.proc.wait(timeout=self.grace)
        except subprocess.TimeoutExpired:
            self.log("[devwatch] child ignored SIGINT; sending SIGKILL")
            self.proc.kill()
            self.proc.wait()

    def _restart(self, changed):
        shown = ", ".join(os.path.relpath(c) for c in changed[:5])
        more = "" if len(changed) <= 5 else f" (+{len(changed) - 5} more)"
        self.log(f"[devwatch] change detected: {shown}{more} — restarting")
        self._stop_child()
        self._spawn()

    def run(self):
        self._spawn()
        snap = self._scan()
        try:
            while True:
                time.sleep(self.interval)
                if self.proc.poll() is not None:
                    self.log(f"[devwatch] child exited (code {self.proc.returncode}); "
                             "waiting for a change to restart")
                new = self._scan()
                changed = changed_files(snap, new)
                if changed:
                    # brief debounce so a burst of saves (editor writing several files) is one
                    # restart, not many; re-scan after the quiet period for the freshest state.
                    time.sleep(min(0.3, self.interval))
                    new = self._scan()
                    self._restart(changed_files(snap, new) or changed)
                    snap = new
        except KeyboardInterrupt:
            self.log("\n[devwatch] shutting down")
        finally:
            self._stop_child()


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="python -m homing_trade.devwatch",
        description="Auto-restart a homing-trade entrypoint on .py/.env changes (dev only).")
    p.add_argument("--interval", type=float, default=1.0,
                   help="seconds between mtime polls (default 1.0)")
    p.add_argument("--watch", default=PKG_DIR,
                   help="directory tree to watch for *.py (default: the homing_trade package)")
    p.add_argument("target", nargs=argparse.REMAINDER,
                   help="entrypoint module + args (default: homing_trade.web). "
                        "Put devwatch flags BEFORE the module.")
    args = p.parse_args(argv)
    command = build_command(args.target)
    Reloader(command, watch_root=args.watch, interval=args.interval).run()


if __name__ == "__main__":
    main()
