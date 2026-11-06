#!/bin/sh
# Start the sound detector test app (uses the model virtualenv).
set -e
cd "$(dirname "$0")/.."
PY=model/.venv/bin/python
[ -x "$PY" ] || { echo "missing $PY (see README, section Model)"; exit 1; }
"$PY" -c "import PyQt6, serial" 2>/dev/null || "$PY" -m pip install -q -r test/requirements.txt
exec "$PY" test/sound_test_gui.py "$@"
