#!/usr/bin/env bash
# skytask_gen starter for Linux/macOS. ./start.sh = run. Or: ./start.sh status | export | preview
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
if [ ! -x .venv/bin/python ]; then
  echo "Creating virtual environment in .venv ..."
  "$PY" -m venv .venv
fi
if [ ! -f .venv/.installed ]; then
  echo "Installing requirements ..."
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
  touch .venv/.installed
fi
if [ $# -eq 0 ]; then
  exec .venv/bin/python -m skytask_gen run
else
  exec .venv/bin/python -m skytask_gen "$@"
fi
