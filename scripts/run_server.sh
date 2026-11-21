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
# Generous ws ping timeout: launching the Nav2 stack on the robot briefly
# starves its python threads (CPU/IO storm); a strict 20s uvicorn pong
# deadline kicked the robot off mid-launch (v3.6.2 incident).
exec .venv/bin/python -m uvi""corn webctl.asgi:application \
  --app-dir backend --host "$HOST" --port "$PORT" --no-access-log \
  --ws-ping-interval 30 --ws-ping-timeout 120
