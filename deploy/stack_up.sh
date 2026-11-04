#!/usr/bin/env bash
# S2000 (Jetson Orin Nano) sensor+drive stack bring-up for wheeltec-webctl.
# Idempotent — safe to re-run any time. Installs as ~/robotctl/stack_up.sh
# on the robot (this file is the source of truth: repo copy = deploy copy).
#
# The Jetson image has NO tmux and NO robot services by default; each unit
# runs via nohup with bash -lc so ~/.profile ROS sourcing applies. Logs:
#   /tmp/base.log /tmp/lidar.log /tmp/webctl-client.log
# Boot persistence: crontab -l line "@reboot sleep 25 && stack_up.sh"
# (no sudo needed).
LOG() { echo "[stack_up] $*"; }

ME_PAT='wheeltec_robot_node$|lslidar_driver_node$|robotctl/client\.py'

# Stop OUR three units only, by explicit PIDs, and WAIT for them to actually
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

sleep 2
nohup bash -lc "set -a; . ~/robotctl/robotctl.env; set +a; \
  source /opt/ros/humble/setup.bash && \
  python3 ~/robotctl/client.py >>/tmp/webctl-client.log 2>&1" >/dev/null 2>&1 &
sleep 2

ok=1
for u in wheeltec_robot_node lslidar_driver_node client.py; do
  if pgrep -U "$(id -u)" -f "$u" >/dev/null; then LOG "$u: OK"; else
    LOG "$u: DEAD (see /tmp logs)"; ok=0
  fi
done
exit $((1 - ok))
