#!/usr/bin/env bash
# Canonical local checks — the same gate CI used to run, now managed in the repo.
# Run manually any time:   bash tools/check.sh
# Runs automatically before every push via the pre-push hook (tools/hooks/pre-push).
set -euo pipefail
cd "$(dirname "$0")/.."

PY="python3"
[ -x ".venv/bin/python" ] && PY=".venv/bin/python"

echo "→ ROADMAP consistency"
"$PY" tools/check_roadmap.py

echo "→ secret guard (no .env / db / keys staged or tracked)"
if git ls-files --error-unmatch .env 2>/dev/null; then
  echo "ERROR: .env is tracked by git — it must stay gitignored." >&2; exit 1
fi

echo "→ test suite"
"$PY" -m pytest -q

echo "✓ all local checks passed"
