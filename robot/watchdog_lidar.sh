#!/usr/bin/env bash
# watchdog_lidar.sh — the vendor lslidar_driver_node on this Jetson is
# crash-prone at runtime (has died with -6 heap-abort and -11 SIGSEGV while
# streaming). cron: */2 * * * * ~/robotctl/watchdog_lidar.sh
# Only restarts the LiDAR; base & webctl client are left alone. The webctl
# client self-heals (it re-subscribes once /scan returns).
LOG=/tmp/webctl-lidar-watchdog.log
pgrep -U "$(id -u)" -f lslidar_driver_node >/dev/null && exit 0
echo "[$(date '+%F %T')] lslidar dead — restarting" >>"$LOG"
# settle: a crash can release the serial port slowly; driver aborts if busy
sleep 2
nohup bash -lc "source /opt/ros/humble/setup.bash && \
  source ~/wheeltec_ros2/install/setup.bash && \
  ros2 launch turn_on_wheeltec_robot wheeltec_lidar.launch.py >>/tmp/lidar.log 2>&1" \
  >/dev/null 2>&1 &
sleep 8
pgrep -U "$(id -u)" -f lslidar_driver_node >/dev/null && echo "  restart OK" >>"$LOG" \
  || echo "  restart FAILED (port still held?) — retrying next tick" >>"$LOG"
