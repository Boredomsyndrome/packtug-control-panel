#!/usr/bin/env bash
# start-all.sh — bring up EVERY robot function with one command
# (S2000 / Jetson Orin Nano). Idempotent — safe to re-run any time.
# Source of truth: repo robot/ folder; installed as ~/robotctl/start-all.sh
# on the robot (tools/deploy_robot.py ships the whole robot/ tree).
#
# Started here (each via nohup bash -lc so ~/.profile ROS sourcing applies):
#   base     wheeltec_robot_node   drive + battery      /tmp/base.log
#   lidar    lslidar_driver_node   /scan                /tmp/lidar.log
#   astra    ASTRA S depth camera  nav-stack sensor     /tmp/astra.log
#   client   robotctl client.py    hub uplink; spawns navd (map/nav/route)
#                                   when WEBCTL_NAV=1    /tmp/webctl-client.log
# Checked, NOT started (root-owned systemd / crontab — install once with the
# setup_* scripts in this same folder):
#   packtug-camera.service         D455 RGB bridge :8080
#   watchdog_lidar.sh  cron */2    revives a crashed lslidar driver
#   watchdog_vpn.sh    cron        keeps the hwdsl2 ppp0 dial up
#
# Boot persistence: crontab -l line "@reboot sleep 25 && bash ~/robotctl/start-all.sh"
# (no sudo needed). Older installs say stack_up.sh in that line — rename once:
#   crontab -l | sed 's/stack_up\.sh/start-all.sh/' | crontab -
LOG() { echo "[start-all] $*"; }

ME_PAT='wheeltec_robot_node$|lslidar_driver_node$|robotctl/client\.py'

# Stop OUR units only, by explicit PIDs, and WAIT for them to actually
# die: the lslidar driver crashes (heap abort) if it opens the serial port
# while a dying predecessor still holds it. 1.5 s settle after death.
my_pids() { pgrep -U "$(id -u)" -f "$ME_PAT" 2>/dev/null; }
my_pids | while read -r p; do kill "$p" 2>/dev/null; done
for _ in $(seq 1 20); do
  [ -z "$(my_pids)" ] || sleep 0.5
  [ -z "$(my_pids)" ] && break
done
[ -n "$(my_pids)" ] && { kill -9 $(my_pids) 2>/dev/null; sleep 1; }
sleep 1.5

ros_launch() { # $1=launch file  $2=log name
  nohup bash -lc "source /opt/ros/humble/setup.bash && \
    source ~/wheeltec_ros2/install/setup.bash && \
    ros2 launch turn_on_wheeltec_robot $1 >/tmp/$2.log 2>&1" >/dev/null 2>&1 &
}

ros_launch base_serial.launch.py base
sleep 2

# LiDAR: one retry for the serial-port race (vendor driver aborts on open fail).
for try in 1 2; do
  ros_launch wheeltec_lidar.launch.py lidar
  sleep 8
  pgrep -U "$(id -u)" -f lslidar_driver_node >/dev/null && break
  [ "$try" -eq 1 ] && LOG "lidar first attempt failed (port busy?), retrying..."
done

# ASTRA S depth camera: upstream ros2_astra_camera overlay ws (~/ws_astra).
# OpenNI protocol — does NOT appear as /dev/video. Start-if-absent and left
# running across re-runs: USB re-enumeration on every bounce is slow/fragile.
if [ -f "$HOME/ws_astra/install/setup.bash" ]; then
  if ! pgrep -f '[a]stra_camera_node' >/dev/null; then
    nohup bash -lc "source /opt/ros/humble/setup.bash && source $HOME/ws_astra/install/setup.bash && ros2 launch astra_camera astra.launch.xml >/tmp/astra.log 2>&1" >/dev/null 2>&1 &
    LOG "astra: launching (log /tmp/astra.log)"; sleep 4
  else
    LOG "astra: already running"
  fi
else
  LOG "astra: ~/ws_astra missing — skipped (git clone orbbec/ros2_astra_camera + colcon build)"
fi

sleep 2
nohup bash -lc "set -a; . ~/robotctl/robotctl.env; set +a; \
  source /opt/ros/humble/setup.bash && \
  python3 ~/robotctl/client.py >>/tmp/webctl-client.log 2>&1" >/dev/null 2>&1 &
sleep 2

# ---- root-owned / cron-owned functions: report, never fight systemd ----
if systemctl list-unit-files packtug-camera.service >/dev/null 2>&1 \
   && systemctl list-unit-files packtug-camera.service 2>/dev/null | grep -q packtug-camera; then
  if systemctl is-active --quiet packtug-camera.service 2>/dev/null; then
    LOG "camera(d455 :8080): OK (systemd)"
  else
    LOG "camera(d455 :8080): NOT running — sudo robot/setup_camera_service.sh (one-time install)"
  fi
else
  LOG "camera(d455 :8080): unit not installed — sudo robot/setup_camera_service.sh"
fi
crontab -l 2>/dev/null | grep -q 'watchdog_lidar.sh' \
  || LOG "note: lidar watchdog not in crontab — add: */2 * * * * bash ~/robotctl/watchdog_lidar.sh >/dev/null 2>&1"
crontab -l 2>/dev/null | grep -q 'watchdog_vpn.sh' \
  || LOG "note: vpn watchdog not in crontab — add: */5 * * * * bash ~/robotctl/watchdog_vpn.sh >/dev/null 2>&1"

ok=1
for u in wheeltec_robot_node lslidar_driver_node client.py; do
  if pgrep -U "$(id -u)" -f "$u" >/dev/null; then LOG "$u: OK"; else
    LOG "$u: DEAD (see /tmp logs)"; ok=0
  fi
done
exit $((1 - ok))
