#!/usr/bin/env bash
# E2E proof for the the server instance: dry-run robot over real ws,
# drive + stop via browser API, verify sink file = what a real base gets.
#
# !! ONLY RUN WHILE THE REAL s2000-1 IS OFFLINE. This registers a dry-run
# !! client under the same id; ws takeover routes test drive commands to
# !! whichever socket holds the hub — never race the real robot.
#    (v3.2: no browser password anymore; API needs no creds.)
set -u
cd "$(dirname "$0")/.."   # repo-relative: fused packtug hub == its own proof
ENVF=".env.server"; [ -f "$ENVF" ] || ENVF=".env"   # packtug keeps server secrets in .env.server
if [ "$(curl -s http://127.0.0.1:8090/api/robot/s2000-1 | python3 -c \
      'import json,sys; print(json.load(sys.stdin).get("online"))' 2>/dev/null)" != "False" ]; then
  echo "ABORT: s2000-1 appears ONLINE — stop the real robot client first (this test takes over its hub slot)"
  exit 1
fi
TOK=$(grep WEBCTL_ROBOTS "$ENVF" | head -1 | cut -d: -f2 | tr -d ', "')
rm -f /tmp/webctl-e2e.json /tmp/e2e-client.log
WEBCTL_SERVER=http://127.0.0.1:8090 WEBCTL_ROBOT_ID=s2000-1 \
WEBCTL_ROBOT_TOKEN=$TOK WEBCTL_DRY_RUN=1 WEBCTL_SINK=/tmp/webctl-e2e.json \
WEBCTL_CAM_MODE=off WEBCTL_LOG_LEVEL=INFO \
  .venv/bin/python robotctl/client.py >/tmp/e2e-client.log 2>&1 &
CPID=$!
sleep 4
echo "drive: $(curl -s -H 'Content-Type: application/json' \
  -d '{"vx":0.25,"wz":0.1}' http://127.0.0.1:8090/api/robot/s2000-1/cmd)"
sleep 1.2
curl -s -X POST http://127.0.0.1:8090/api/robot/s2000-1/stop >/dev/null
sleep 1
kill $CPID 2>/dev/null
echo "--- client ws lines:"; grep -E "websocket|watchdog" /tmp/e2e-client.log | head -3
echo "--- client lidar lines:"; grep -i "lidar" /tmp/e2e-client.log | head -2
echo "--- sink (what the robot base received):"; tail -2 /tmp/webctl-e2e.json
echo "--- lidar scan via API:"; curl -s \
  http://127.0.0.1:8090/api/robot/s2000-1/scan | head -c 260; echo
echo "--- fleet status:"; curl -s http://127.0.0.1:8090/api/robot/s2000-1 | head -c 320
echo
