#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
firebase deploy --only hosting --project sky-task-c4048
