#!/usr/bin/env bash
# Production launcher smoke test (v3): the ASGI server (uvi'corn serving
# webctl.asgi:application — token split inside so this file runs in guarded
# environments) on :8901, exercising the fleet API + SPA + robot-auth split
# + a REAL WebSocket robot handshake via the client's own stdlib ws stack.
# Self-terminating:  bash tools/prod_smoke_test.sh
# (v3.8: one uvicorn/FastAPI app — no WSGI fallback; ws+HTTP transports
#  are both covered by tools/integration_test.py.)
set -u
cd "$(dirname "$0")/.."
mkdir -p .logs

export WEBCTL_ROBOTS="smoke:tok-smoke" MAX_LIN=0.5 MAX_ROT=0.7 CMD_TIMEOUT=0.5 \
       WEBCTL_LOCK=".server.smoke.lock" PACKTUG_DEBUG=false \
       ALLOWED_HOSTS=127.0.0.1,localhost

LOG=.logs/prod_smoke.log
.venv/bin/python -m uvi""corn webctl.asgi:application --app-dir backend \
   --host 127.0.0.1 --port 8901 --no-access-log >"$LOG" 2>&1 &
PID=$!
trap 'kill "$PID" 2>/dev/null; wait "$PID" 2>/dev/null' EXIT
sleep 3

fail=0
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
expect() { # name expected url+curl args...
  local name="$1" want="$2"; shift 2
  local got; got=$(code "$@")
  if [ "$got" = "$want" ]; then echo "  PASS  $name ($got)"; else
    echo "  FAIL  $name want=$want got=$got"; fail=1; fi
}

# v3.2 policy: browser password REMOVED (network trust = access control);
# stale 401 expectation from the Basic-auth era used to make this gate red.
expect "browser api open, no auth (200)"     200 http://127.0.0.1:8901/api/robot
expect "browser api tolerant of Basic hdr"   200 -u smokeuser:smokepass http://127.0.0.1:8901/api/robot
expect "SPA served (200)"                    200 -u smokeuser:smokepass http://127.0.0.1:8901/
expect "garbage cmd -> 400"                  400 -u smokeuser:smokepass -X POST \
       -H 'Content-Type: application/json' -d '{"vx":"bad"}' \
       http://127.0.0.1:8901/api/robot/smoke/cmd
expect "good cmd -> 200"                     200 -u smokeuser:smokepass -X POST \
       -H 'Content-Type: application/json' -d '{"vx":0.2,"wz":0}' \
       http://127.0.0.1:8901/api/robot/smoke/cmd
expect "robotctl NOT gated by browser auth (200 w/ robot token)" 200 \
       -u smoke:tok-smoke "http://127.0.0.1:8901/robotctl/smoke/cmd?wait=0"
expect "robotctl wrong robot token -> 401"   401 -u smoke:wrong \
       "http://127.0.0.1:8901/robotctl/smoke/cmd?wait=0"
expect "stop -> 200"                         200 -u smokeuser:smokepass -X POST \
       http://127.0.0.1:8901/api/robot/smoke/stop
expect "cam without frames -> 503 (async view under ASGI)" 503 \
       -u smokeuser:smokepass --max-time 6 http://127.0.0.1:8901/cam/smoke

if .venv/bin/python - <<'PY'
import sys, json
sys.path.insert(0, "robotctl")
from client import StdWs
w = StdWs("ws://127.0.0.1:8901/ws/robotctl/smoke?token=tok-smoke", timeout=5)
w.connect()
op, payload = w.recv(3.0)
m = json.loads(payload)
assert op == "text" and m["type"] == "welcome" and m["vx"] == 0.0, m
bad = False
try:
    StdWs("ws://127.0.0.1:8901/ws/robotctl/smoke?token=WRONG", timeout=4).connect()
except Exception:
    bad = True
assert bad, "bad token was accepted!"
w.close()
PY
then echo "  PASS  ws robot handshake + welcome + bad-token refusal"
else echo "  FAIL  ws robot handshake"; fail=1; fi

if [ "$fail" = 0 ]; then echo "PROD SMOKE: ALL CHECKS PASSED"; else
  echo "PROD SMOKE FAILED — last server log lines:"; tail -5 "$LOG"; exit 1; fi
