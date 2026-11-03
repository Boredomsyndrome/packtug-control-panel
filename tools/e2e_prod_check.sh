#!/usr/bin/env bash
# E2E proof for the potatoserver instance: dry-run robot over real ws,
# drive + stop via browser API, verify sink file = what a real base gets.
set -u
cd /home/ubuntu/wheeltec-webctl
TOK=$(grep WEBCTL_ROBOTS .env | cut -d: -f2)
PASS=$(grep WEBCTL_AUTH_PASS .env | cut -d= -f2)
rm -f /tmp/webctl-e2e.json /tmp/e2e-client.log
WEBCTL_SERVER=http://127.0.0.1:8090 WEBCTL_ROBOT_ID=s2000-1 \
WEBCTL_ROBOT_TOKEN=$TOK WEBCTL_DRY_RUN=1 WEBCTL_SINK=/tmp/webctl-e2e.json \
WEBCTL_CAM_MODE=off WEBCTL_LOG_LEVEL=INFO \
  .venv/bin/python robotctl/client.py >/tmp/e2e-client.log 2>&1 &
CPID=$!
sleep 4
echo "drive: $(curl -s -u comet:$PASS -H 'Content-Type: application/json' \
  -d '{"vx":0.25,"wz":0.1}' http://127.0.0.1:8090/api/robot/s2000-1/cmd)"
sleep 1.2
curl -s -u comet:$PASS -X POST http://127.0.0.1:8090/api/robot/s2000-1/stop >/dev/null
sleep 1
kill $CPID 2>/dev/null
echo "--- client ws lines:"; grep -E "websocket|watchdog" /tmp/e2e-client.log | head -3
echo "--- client lidar lines:"; grep -i "lidar" /tmp/e2e-client.log | head -2
echo "--- sink (what the robot base received):"; tail -2 /tmp/webctl-e2e.json
echo "--- lidar scan via API:"; curl -s -u comet:$PASS \
  http://127.0.0.1:8090/api/robot/s2000-1/scan | head -c 260; echo
echo "--- fleet status:"; curl -s -u comet:$PASS http://127.0.0.1:8090/api/robot/s2000-1 | head -c 320
echo
