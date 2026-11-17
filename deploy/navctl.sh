#!/usr/bin/env bash
# navctl.sh — vendor Nav2/slam_toolbox bring-up for autonomous navigation.
# Repo-managed (deploy/), installed to ~/robotctl/. NEVER edits ~/wheeltec_ros2;
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

cmd="$1"
[ $# -gt 0 ] && shift

source "$ROS_SRC" 2>/dev/null
[ -f "$WS/install/setup.bash" ] && source "$WS/install/setup.bash" 2>/dev/null
mkdir -p "$MAPS"

# v3.6.5 — FastDDS SHM starves slam's /tf subscription on this loaded Jetson:
# a FRESH slam dropped every scan ("frame 'laser' ... queue is full") while
# concurrent tf2_echo probes streamed the same transforms fine — the exact
# SHM-exhaustion family documented in the v3.4.4 pose saga, now hitting slam
# itself. Force UDP-only RTPS transport (no SHM) for everything the nav plane
# launches; setsid children below inherit this env.
cat > "$NAVHOME/fastdds_udp_only.xml" <<'XML'
<?xml version="1.0" encoding="UTF-8" ?>
<profiles xmlns="http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles">
    <transport_descriptors>
        <transport_descriptor>
            <transport_id>udp_only</transport_id>
            <type>UDPv4</type>
        </transport_descriptor>
    </transport_descriptors>
    <participant profile_name="udp_only" is_default_profile="true">
        <rtps>
            <userTransports>
                <transport_id>udp_only</transport_id>
            </userTransports>
            <useBuiltinTransports>false</useBuiltinTransports>
        </rtps>
    </participant>
</profiles>
XML
export FASTRTPS_DEFAULT_PROFILES_FILE="$NAVHOME/fastdds_udp_only.xml"

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
  # vendor's proven pre-slam stage (their prod_bringup launches it "once for
  # all runtime modes"; every vendor consumer subscribes /scan_filtered).
  # Binary is in the robot's EXISTING vendor build ($WS/install) — read-only
  # use, nothing installed, nothing compiled.
  pgrep -f "[s]can_box_filter_node" >/dev/null && return 0
  [ -x "$WS/install/wheeltec_slam_toolbox/lib/wheeltec_slam_toolbox/scan_box_filter_node" ] || \
    [ -f "$FILTER_PARAMS" ] || { echo "WARN: no scan_box_filter_node / $FILTER_PARAMS — slam falls back to RAW /scan"; return 1; }
  setsid bash -c "source $ROS_SRC && source $WS/install/setup.bash && exec ros2 run wheeltec_slam_toolbox scan_box_filter_node --ros-args -r __node:=scan_box_filter_node --params-file $FILTER_PARAMS" >/tmp/nav_filter.log 2>&1 </dev/null &
  for t in $(seq 1 10); do sleep 1; pgrep -f "[s]can_box_filter_node" >/dev/null && break; done
  pgrep -f "[s]can_box_filter_node" >/dev/null && echo "scan filter up (/scan -> /scan_filtered)" || echo "WARN: scan filter failed to start — tail /tmp/nav_filter.log"
}

case "$cmd" in
  map-on)
    ensure_tf
    ensure_filter || true
    if proc "[a]sync_slam_toolbox_node"; then echo "mapping already up"; exit 0; fi
    setsid bash -c "source $ROS_SRC && source $WS/install/setup.bash && exec ros2 run slam_toolbox async_slam_toolbox_node --ros-args -p params_file:=$MAPPER --remap odom:=odom_combined" >/tmp/nav_map.log 2>&1 </dev/null &
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
      echo "FAILED: map_saver produced no files — tail /tmp/nav_saver.log"; exit 1
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
    pkill -f 'scan_box_filter_node'  2>/dev/null
    
    sleep 2
    pkill -9 -f 'bringup_launch.py'   2>/dev/null
    pkill -9 -f 'component_container' 2>/dev/null
    pkill -9 -f 'async_slam_toolbox_node' 2>/dev/null
    pkill -9 -f 'map_saver_cli'       2>/dev/null
    pkill -9 -f 'webctl_navctl_tf'    2>/dev/null
    pkill -9 -f 'webctl_navctl_ekf'   2>/dev/null
    pkill -9 -f 'scan_box_filter_node' 2>/dev/null
    
    sleep 2
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
  *)
    echo "usage: navctl.sh map-on|map-save <name>|stack-on <name>|stop|status"
    exit 2
    ;;
esac
