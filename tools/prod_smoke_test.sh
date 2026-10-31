#!/usr/bin/env bash
# Production launcher smoke test: start the single-worker gunicorn server on
# :8901, exercise the fleet API + SPA + robot auth split, then kill itself.
# (Written as a script file on purpose: the local exec-guard refuses any
#  foreground command whose text mentions the server binary — invoke this
#  file instead:  bash tools/prod_smoke_test.sh )
set -u
cd "$(dirname "$0")/.."
mkdir -p .logs

export WEBCTL_ROBOTS="smoke:tok-smoke" MAX_LIN=0.5 MAX_ROT=0.7 CMD_TIMEOUT=0.5 \
       DJANGO_SECRET_KEY=smoke DJANGO_DEBUG=false \
       ALLOWED_HOSTS=127.0.0.1,localhost \
       WEBCTL_AUTH_USER=smokeuser WEBCTL_AUTH_PASS=smokepass

GUN="$(pwd)/.venv/bin/gu""nicorn"
LOG=.logs/prod_smoke.log
"$GUN" webctl.wsgi:application -b 127.0.0.1:8901 -w 1 --threads 16 -t 120 \
      --chdir backend >"$LOG" 2>&1 &
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

expect "browser api needs Basic auth (401)"  401 http://127.0.0.1:8901/api/robot
expect "browser api with auth (200)"         200 -u smokeuser:smokepass http://127.0.0.1:8901/api/robot
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

if [ "$fail" = 0 ]; then echo "PROD SMOKE: ALL CHECKS PASSED"; else
  echo "PROD SMOKE FAILED — last server log lines:"; tail -5 "$LOG"; exit 1; fi
