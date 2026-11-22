#!/usr/bin/env bash
# navctl.sh v4.1.0 — vendor Nav2/slam_toolbox bring-up for autonomous navigation.
# Repo-managed (robot/), installed to ~/robotctl/. NEVER edits ~/wheeltec_ros2;
# only launches vendor packages with vendor parameter files.
#
#   navctl.sh map-on              slam_toolbox (async) + vendor online_async params
#   navctl.sh map-save <name>     map_saver_cli -> ~/robotctl/maps/<name>.{pgm,yaml}
#   navctl.sh stack-on <name>     wheeltec_nav2 bringup (AMCL+Nav2+waypoint_follower)
#                                 slam tool is stopped first (map->odom owner)
#   navctl.sh stop                kill nav stack + slam (TERM, then -9). Leaves
#                                 base / lidar / astra / teleop stack ALONE.
#   navctl.sh status              prints "running ..." for any live nav plane
#
# Pitfalls honoured: NO `set -eu` around ROS sourcing (setup.bash can fail
# under -u and kills the shell); pkill patterns here can never match this
# script's own cmdline (which is only "bash .../navctl.sh stop").
WS="$HOME/wheeltec_ros2"
NAVHOME="$HOME/robotctl"
MAPS="$NAVHOME/maps"
ROS_SRC="/opt/ros/humble/setup.bash"
NAVCTL_VERSION="v4.1.0"   # == repo VERSION (CS-I3); `navctl.sh version` reports it
PARAM="$WS/src/wheeltec_robot_nav2/param/wheeltec_param/param_four_wheel_diff_bs.yaml"
# v3.6.5 FINAL: vendor's proven rplidar slam profile (repo copy, not their
# file — vendor tree stays untouched). THE fix: their chain runs the whole
# stack on /scan_filtered (box-filtered raw scans), NOT raw /scan. Raw A1/A2
# scans here are ~64 KB (multi-return, 4x normal) — the exact payload class
# this Jetson's DDS path chokes on; 'queue is full' was slam drowning in raw
# /scan. Their own nav2 params + prod_bringup confirm: every consumer uses
# /scan_filtered via wheeltec_slam_toolbox/scan_box_filter_node.
MAPPER="$NAVHOME/nav_params/mapper_rplidar_a1.yaml"
FILTER_PARAMS="$NAVHOME/nav_params/laser_filter.yaml"

# v3.7 (merge, teammate-fork ACCEPT): render a Nav2 PARAM COPY — the vendor
# tree stays untouched. Two proven injections:
#  1. costmap/AMCL scan topics -> /scan_filtered (the box-masked, odom-
#     restamped stream slam already uses; raw /scan sees the towed cage as
#     an obstacle welded to the chassis — their fleet runs the whole stack
#     on /scan_filtered, matching our own v3.6.5 lesson);
#  2. default_nav_to_pose_bt_xml -> no-spin/no-backup recovery tree (a
#     towed cage must never spin in place or reverse for recovery).
# Frozen scope: cage velocity/footprint tuning is NOT included (awaits a
# Comet cage-profile decision, see docs/MERGE plan §1.1 note).
PARAM_COPY="$NAVHOME/nav_params/param_four_wheel_diff_bs.webctl.yaml"
if [ -f "$PARAM" ]; then
  mkdir -p "$NAVHOME/nav_params"
  if python3 - "$PARAM" "$PARAM_COPY" "$NAVHOME" <<'PY'
import os
import sys

src, dst, navhome = sys.argv[1], sys.argv[2], sys.argv[3]
s = open(src, encoding="utf-8").read()
s = s.replace("    scan_topic: scan", "    scan_topic: /scan_filtered")
s = s.replace("          topic: /scan", "          topic: /scan_filtered")
if "default_nav_to_pose_bt_xml" not in s:
    s = s.replace("bt_navigator:\n  ros__parameters:\n",
                  "bt_navigator:\n  ros__parameters:\n"
                  "    default_nav_to_pose_bt_xml: "
                  "\"%s/nav_params/navigate_no_spin_backup.xml\"\n"
                  % navhome, 1)
open(dst + ".tmp", "w", encoding="utf-8").write(s)
os.replace(dst + ".tmp", dst)
print("param-render scan_remap=%s bt=%s" %
      ("scan_topic: scan" not in s, "default_nav_to_pose_bt_xml" in s))
PY
  then
    PARAM="$PARAM_COPY"
  fi
fi

cmd="$1"
[ $# -gt 0 ] && shift

source "$ROS_SRC" 2>/dev/null
[ -f "$WS/install/setup.bash" ] && source "$WS/install/setup.bash" 2>/dev/null
mkdir -p "$MAPS"

# v3.6.5 POST-MORTEM (do NOT reintroduce any FASTRTPS_DEFAULT_PROFILES_FILE):
# a custom profile file — even a 'bigger SHM' one — partitions this robot's
# DDS plane: navctl-spawned processes went invisible to the vendor stack
# after reboot (their TX reached nobody; only camera/odom/lidar flowed).
# Env diff of one island vs healthy vendor process had EXACTLY one line:
# FASTRTPS_DEFAULT_PROFILES_FILE=fastdds_nav.xml. Stock profile only.
# v3.6.5 NOTE: the "starvation" theory (slam blind to /tf+/scan) is now most
# likely NOT transport: slam was pointed at RAW /scan (64 KB multi-return
# A2-class scans) while short-lived probes grabbed one small message at a
# time. UDP-only profile kept anyway (harmless; vendor-neutral). CycloneDDS
# experiment dropped — no apt installs, per owner: nav is stock ROS2 here.

has_topic() { timeout 6 ros2 topic list 2>/dev/null | grep -qx "$1"; }
has_topic_like() { timeout 6 ros2 topic list 2>/dev/null | grep -q "$1"; }
# ros2 CLI probes need the ros2 daemon — cold daemon = 40s per probe = every
# bounded check FAILS. Process checks are instant and daemon-free; the
# [x] bracket stops the script matching its own argv.
proc() { pgrep -f "$1" >/dev/null 2>&1; }

# The vendor robot_mode_description.launch.py gates the four_wheel_diff_bs
# group on IfCondition(senior_mec_dl) — a copy-paste bug upstream, so the
# S2000 chassis NEVER gets base_footprint->laser TF and slam_toolbox drops
# every scan as "timestamp too old". We can't patch ~/wheeltec_ros2, so we
# re-publish the exact same static transforms (from their launch source)
# scoped to the nav plane: started on map-on/stack-on, killed on stop.
# webctl_robot_client.py's cmdline contains 'webctl_navctl' too — excluded.
ensure_tf() {
  # v3.6.5 CRITICAL (the 'queue is full' finale): humble's static_transform_
  # publisher latches /tf_static ONCE, and this Jetson's DDS never delivers
  # that latch to slam's late-joining TRANSIENT_LOCAL listener — slam's buffer
  # never knew frame 'laser', dropped 100% of scans, map frame NEVER existed,
  # pose=None forever. `--period` (continuous republish) is iron+/rolling only
  # (humble exits 1 — verified). webctl_tf_repeater.py streams the same
  # transform on LIVE /tf at 5 Hz, the path tf2_echo proved works. The
  # trailing 'webctl_navctl_tf' argv marker keeps ensure_tf's guard AND
  # navctl stop's pkill matching.
  pgrep -f "webctl_tf_repeater|webctl_navctl_tf" >/dev/null || \
  setsid bash -c "source $ROS_SRC && source $WS/install/setup.bash && exec python3 $NAVHOME/webctl_tf_repeater.py webctl_navctl_tf" >>/tmp/nav_tf.log 2>&1 </dev/null &
  # NOTE (verified 2026-11-09): the vendor mapper params set NO frame ids, so
  # publishes NO TF — we run robot_localization EKF ourselves (odometry-only:
  # the stock config wants /imu/data_raw which the base node never feeds on
  # this robot, and ships publish_tf:false). ~/robotctl/ekf_navctl.yaml =
  # vendor ekf_carto.yaml with imu0 stripped and publish_tf:true.
  pgrep -f "webctl_navctl_ekf" >/dev/null || \
  setsid bash -c "source $ROS_SRC && source $WS/install/setup.bash && exec ros2 run robot_localization ekf_node --ros-args -r __node:=webctl_navctl_ekf --params-file $NAVHOME/ekf_navctl.yaml -r /odometry/filtered:=odom_combined" >>/tmp/nav_ekf.log 2>&1 </dev/null &
  sleep 2
}

ensure_filter() {
  # vendor architecture: slam + costmaps consume /scan_filtered, never raw
  # /scan (v3.6.5). Robot's vendor build lacks the vendor filter binary and
  # apt installs are forbidden -> stock-ROS2 equivalent webctl_scan_filter.py
  # (box mask + RELIABLE republish + odom-timeline restamp, see its header).
  pgrep -f "[w]ebctl_scan_filter|[s]can_box_filter_node" >/dev/null && return 0
  [ -f "$NAVHOME/webctl_scan_filter.py" ] || { echo "WARN: no webctl_scan_filter.py — slam falls back to RAW /scan"; return 1; }
  setsid bash -c "source $ROS_SRC && exec python3 $NAVHOME/webctl_scan_filter.py webctl_navctl_filter_marker" >/tmp/nav_filter.log 2>&1 </dev/null &
  for t in $(seq 1 10); do sleep 1; pgrep -f "[w]ebctl_scan_filter" >/dev/null && break; done
  pgrep -f "[w]ebctl_scan_filter" >/dev/null && echo "scan filter up (/scan -> /scan_filtered)" || echo "WARN: scan filter failed — tail /tmp/nav_filter.log"
}

case "$cmd" in
  map-on)
    ensure_tf
    ensure_filter || true
    if proc "[a]sync_slam_toolbox_node"; then echo "mapping already up"; exit 0; fi
    setsid bash -c "source $ROS_SRC && source $WS/install/setup.bash && exec ros2 run slam_toolbox async_slam_toolbox_node --ros-args --params-file $MAPPER --remap odom:=odom_combined" >/tmp/nav_map.log 2>&1 </dev/null &
    for t in $(seq 1 20); do sleep 1; proc "[a]sync_slam_toolbox_node" && break; done
    if proc "[a]sync_slam_toolbox_node"; then echo "mapping up"; else
      echo "FAILED: slam_toolbox died at startup — tail /tmp/nav_map.log"; exit 1; fi
    ;;
  map-save)
    name="${1:-room1}"
    proc "[a]sync_slam_toolbox_node" || proc "[m]ap_server" || { echo "FAILED: no live map publisher — start mapping or nav first"; exit 1; }
    ros2 run nav2_map_server map_saver_cli -f "$MAPS/$name" >/tmp/nav_saver.log 2>&1
    if [ -f "$MAPS/$name.pgm" ] && [ -f "$MAPS/$name.yaml" ]; then
      echo "saved $MAPS/$name"
    else
      echo "FAILED[$NAVCTL_VERSION]: map_saver produced no files — tail /tmp/nav_saver.log"; exit 1
    fi
    ;;
  stack-on)
    ensure_tf
    ensure_filter || true
    name="${1:-room1}"
    [ -f "$MAPS/$name.yaml" ] || { echo "FAILED: map '$name' not saved"; exit 1; }
    # slam_toolbox and AMCL both want to own map->odom: hand the TF over.
    pkill -f async_slam_toolbox_node 2>/dev/null
    sleep 2
    # nice/ionice: the stack launch must not out-pollute the robotctl client
    # for CPU+disk (launch storm starved its ws keepalive → hub drops).
    setsid nice -n 5 ionice -c3 bash -c "source $ROS_SRC && source $WS/install/setup.bash && exec ros2 launch wheeltec_nav2 bringup_launch.py slam:=False autostart:=true map:=$MAPS/$name.yaml params_file:=$PARAM" >/tmp/nav_stack.log 2>&1 </dev/null &
    sleep 10
    if proc "[b]ringup_launch.py"; then
      echo "nav up (map=$name)"
    else
      echo "FAILED: Nav2 launch died — tail /tmp/nav_stack.log"; exit 1
    fi
    ;;
  stop)
    pkill -f 'bringup_launch.py'      2>/dev/null
    pkill -f 'component_container'    2>/dev/null
    pkill -f 'async_slam_toolbox_node' 2>/dev/null
    pkill -f 'map_saver_cli'          2>/dev/null
    pkill -f 'webctl_navctl_tf'       2>/dev/null
    pkill -f 'webctl_navctl_ekf'     2>/dev/null
    pkill -f 'webctl_scan_filter'        2>/dev/null
    
    sleep 2
    pkill -9 -f 'bringup_launch.py'   2>/dev/null
    pkill -9 -f 'component_container' 2>/dev/null
    pkill -9 -f 'async_slam_toolbox_node' 2>/dev/null
    pkill -9 -f 'map_saver_cli'       2>/dev/null
    pkill -9 -f 'webctl_navctl_tf'    2>/dev/null
    pkill -9 -f 'webctl_navctl_ekf'   2>/dev/null
    pkill -9 -f 'webctl_scan_filter'       2>/dev/null
    
    sleep 2
    rm -f /dev/shm/fastrtps_* /dev/shm/fastdds_* 2>/dev/null || true
    if proc "[a]sync_slam_toolbox_node" || proc "[b]ringup_launch.py"; then
      echo "WARN: nav processes still live after stop"; exit 1
    fi
    echo "nav stopped"
    ;;
  status)
    state=""
    proc "[a]sync_slam_toolbox_node" && state="$state mapping"
    proc "[b]ringup_launch.py" && state="$state localizing"
    [ -n "$state" ] && { echo "running$state"; exit 0; }
    echo "idle"; exit 0
    ;;
  version)
    echo "$NAVCTL_VERSION"; exit 0
    ;;
  *)
    echo "usage: navctl.sh version|map-on|map-save <name>|stack-on <name>|stop|status"
    exit 2
    ;;
esac
