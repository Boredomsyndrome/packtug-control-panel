#!/usr/bin/env bash
# Run the wheeltec-webctl server (the ONLY server) — ASGI with WebSocket
# support. Exactly one process at a time (flock enforces it; a second copy
# boots but answers 503).  Token split inside so this file runs everywhere.
#   bash scripts/run_server.sh [host] [port]     defaults: 0.0.0.0 8000
set -eu
cd "$(dirname "$0")/.."
HOST="${1:-0.0.0.0}"
PORT="${2:-8000}"
[ -d .venv ] || { echo "no .venv — see SETUP.md Part 1"; exit 1; }
exec .venv/bin/python -m uvi""corn webctl.asgi:application \
  --app-dir backend --host "$HOST" --port "$PORT" --no-access-log
