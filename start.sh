#!/usr/bin/env bash
# Start Agent Office on macOS / Linux. Opens http://localhost:8000 (demo mode until you connect an AI).
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
"$PY" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" || { echo "Python 3.10+ needed"; exit 1; }
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
( sleep 4; command -v xdg-open >/dev/null && xdg-open http://localhost:8000 || open http://localhost:8000 ) >/dev/null 2>&1 &
exec .venv/bin/python run.py
