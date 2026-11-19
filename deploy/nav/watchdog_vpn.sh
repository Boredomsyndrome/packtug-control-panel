#!/usr/bin/env bash
# watchdog_vpn.sh — robot-side link self-heal (cron */2).
# If the hub stops answering from the robot's own view, first redial the
# NetworkManager L2TP VPN; if the link is STILL down one cycle later,
# bounce the robotctl client (covers the half-open-socket class where the
# client process is alive but its transport thread is stuck pre-redial).
# Client restart is motion-safe: SIGTERM path flushes zero velocity and
# the setsid'd nav plane survives; navd resyncs mode on boot.
ENVF="$HOME/robotctl/robotctl.env"
LOG=/tmp/watchdog_vpn.log
HUBS=$(sed -n 's/^WEBCTL_SERVER=//p' "$ENVF" 2>/dev/null | head -1 | tr -d '"' | tr ',' ' ')
[ -n "$HUBS" ] || HUBS="http://localhost:8090"
RID=$(sed -n 's/^WEBCTL_ROBOT_ID=//p' "$ENVF" 2>/dev/null | tr -d '"' | head -1)
[ -n "$RID" ] || RID="wheeltec"

# Content check, not status check: a stranger device on the school LAN can
# answer ANY url on 192.168.1.x — only our hub echoes the robot id in JSON.
ok() {
  for h in $HUBS; do
    curl -s -m 6 "$h/api/robot" 2>/dev/null | grep -q "\"id\":\"$RID\"" && return 0
  done
  return 1
}
ok && exit 0

echo "$(date '+%F %T') hub unreachable via [$HUBS] — redial VPN" >> "$LOG"
VPN=$(nmcli -t con show --active | awk -F: '$1 ~ /^vpn/ {print $2}' | head -1)
[ -n "$VPN" ] && nmcli -t con up id "$VPN" >/dev/null 2>&1
sleep 12
if ok; then
  echo "$(date '+%F %T') VPN redial healed link" >> "$LOG"
  exit 0
fi

LAST=$(stat -c %Y /tmp/wdvpn.lastbounce 2>/dev/null || echo 0)
NOW=$(date +%s)
if [ $((NOW - LAST)) -ge 240 ]; then
  echo "$(date '+%F %T') link still down — bouncing robotctl client" >> "$LOG"
  touch /tmp/wdvpn.lastbounce
  pkill -f '[c]lient\.py' 2>/dev/null
  pkill -f '[r]un_client' 2>/dev/null
  sleep 2
  setsid bash -lc 'set -a; . ~/robotctl/robotctl.env; set +a; source /opt/ros/humble/setup.bash && python3 ~/robotctl/client.py >>/tmp/webctl-client.log 2>&1' </dev/null >/dev/null 2>&1 &
fi
exit 0
