#!/usr/bin/env bash
# Dev auto-reloader — restarts the bot on any .py/.env change (nodemon for Python).
#   tools/dev.sh                       # reloads `python -m homing_trade.web`
#   tools/dev.sh homing_trade.daemon   # reload a different entrypoint
#   tools/dev.sh homing_trade.web --no-browser
# Uses the repo venv python when present. NOT for production (use the daemon/supervisor there).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="./.venv/bin/python"
[ -x "$PY" ] || PY="python3"
exec "$PY" -m homing_trade.devwatch "$@"
