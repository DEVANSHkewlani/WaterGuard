#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
PYTHON_BIN=''
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    PYTHON_BIN="$candidate"
    break
  fi
done
if [ -z "$PYTHON_BIN" ]; then
  echo 'Python 3.11 or newer is required. Install it and rerun this script.'
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  "$PYTHON_BIN" -m venv .venv
fi
VENV_PYTHON=".venv/bin/python"
if ! "$VENV_PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  echo 'The existing .venv uses Python older than 3.11. Recreate it with Python 3.11 or newer.'
  exit 1
fi
"$VENV_PYTHON" -m pip install -r requirements.txt
"$VENV_PYTHON" - <<'PY'
from backend.fingerprints import load_catalog, build_catalog
try:
    load_catalog('Net1')
except (FileNotFoundError, ValueError):
    print('Preparing Net1 fault signatures...')
    build_catalog('Net1')
PY
echo 'Open http://127.0.0.1:8000/ in your browser. Keep this terminal running.'
"$VENV_PYTHON" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
