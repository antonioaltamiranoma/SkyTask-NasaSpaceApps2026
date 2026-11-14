#!/usr/bin/env bash
# Sky Task service, local: API at http://localhost:8080/v1 , website at http://localhost:8080/
# Dev inspector routes (/v1/dev/...) are ON here. Never use this script on a server.
set -euo pipefail
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -q -r requirements-dev.txt
[ -f settings.local.toml ] || cp settings.local.example.toml settings.local.toml
export SKYTASK_DEV=1
echo "Sky Task local service: http://localhost:8080/  (API docs: /docs)"
exec .venv/bin/python -m uvicorn --factory app.main:create_app --host 127.0.0.1 --port "${PORT:-8080}"
