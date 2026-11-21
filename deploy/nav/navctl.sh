#!/usr/bin/env bash
# generated-from: packtug upstream ca265cc:deploy/nav/navctl.sh — FROZEN REFERENCE, not deployed (see README.md)
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
NAVCTL_VERSION="v3.6.6-map-save-timeout"
ROS_SRC="/opt/ros/humble/setup.bash"
PARAM_SRC="$WS/src/wheeltec_robot_nav2/param/wheeltec_param/param_four_wheel_diff_bs.yaml"
# The vendor AMCL/costmap params subscribe to raw /scan. On this robot raw
# scans carry lidar-clock stamps that AMCL's TF buffer rejects ("timestamp
# earlier than all the data"), so every scan is dropped and no map pose is
# ever produced. Our scan filter restamps to the odom timeline and publishes
# /scan_filtered; point the Nav2 consumers at that. Vendor tree untouched.
PARAM="$PARAM_SRC"
if [ -f "$PARAM_SRC" ]; then
  PARAM_OVERRIDE="$NAVHOME/nav_params/param_four_wheel_diff_bs_filtered.yaml"
  mkdir -p "$(dirname "$PARAM_OVERRIDE")"
  python3 - "$PARAM_SRC" "$PARAM_OVERRIDE" <<'PY'
import re
import sys

src, dst = sys.argv[1], sys.argv[2]
s = open(src, encoding="utf-8").read()
# scans: AMCL + both costmaps must consume the re-stamped filtered stream
s = s.replace("    scan_topic: scan", "    scan_topic: /scan_filtered")
s = s.replace("          topic: /scan", "          topic: /scan_filtered")
# towed-cage tuning: slower, no reverse, more clearance. Allow v=0 so the
# controller can stop / make the small final yaw correction instead of
# circling and tripping the progress checker.
s = s.replace("max_vel_x: 0.3", "max_vel_x: 0.2")
s = s.replace("max_vel_theta: 0.5", "max_vel_theta: 0.35")
s = s.replace("min_vel_x: -0.3", "min_vel_x: 0.0")
s = s.replace("inflation_radius: 0.45", "inflation_radius: 0.65")
# goal: vendor 0.25 xy; loose yaw so a trailer does not circle at the point
s = s.replace("yaw_goal_tolerance: 0.25", "yaw_goal_tolerance: 0.5")
# do not abort just because the final alignment is slow
s = s.replace("required_movement_radius: 0.5", "required_movement_radius: 0.25")
s = s.replace("movement_time_allowance: 10.0", "movement_time_allowance: 30.0")
# no backup/spin recoveries: use the repo BT that only clears and waits
s = s.replace("bt_navigator:\n  ros__parameters:\n",
              "bt_navigator:\n  ros__parameters:\n"
              "    default_nav_to_pose_bt_xml: "
              "\"/home/wheeltec/robotctl/nav_params/navigate_no_spin_backup.xml\"\n", 1)
# footprint covers car + towed cage (cage end ~45 cm behind the car rear)
s = re.sub(r'footprint:\s*"[^"]*"',
           'footprint: "[ [-0.98, -0.50],[-0.98, 0.50],'
           '[0.13, 0.50],[0.13, -0.50] ]"', s)
open(dst, "w", encoding="utf-8").write(s)
PY
  PARAM="$PARAM_OVERRIDE"
fi
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

# v3.6.5 FINAL transport lever (saga's "SHM size exhaustion" note, made
# actionable): humble FastDDS ships SHM pool ~14 MB; camera+scan+tf+odom
# reliable depth-10 pools on this loaded Orin-class Jetson overflow it and
# long-lived subscribers silently starve (slam: /tf never resolves; fresh
# short probes unaffected — exactly our symptom table). Give the nav plane
# a 192 MB SHM file + 4 MB segments; vendor default untouched.
cat > "$NAVHOME/fastdds_nav.xml" <<'XML'
<?xml version="1.0" encoding="UTF-8" ?>
<profiles xmlns="http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles">
    <transport_descriptors>
        <transport_descriptor>
            <transport_id>nav_shm_big</transport_id>
            <type>SHM</type>
            <segment_size>4194304</segment_size>
            <port_queue_capacity>1048576</port_queue_capacity>
        </transport_descriptor>
        <transport_descriptor>
            <transport_id>nav_udp</transport_id>
            <type>UDPv4</type>
        </transport_descriptor>
    </transport_descriptors>
    <participant profile_name="nav_shm_big" is_default_profile="true">
        <rtps>
            <userTransports>
                <transport_id>nav_shm_big</transport_id>
                <transport_id>nav_udp</transport_id>
            </userTransports>
            <!-- builtin OFF: else the default 14 MB SHM transport coexists
                 and FastDDS may still route local traffic through it -->
            <useBuiltinTransports>false</useBuiltinTransports>
        </rtps>
    </participant>
</profiles>
XML
export FASTRTPS_DEFAULT_PROFILES_FILE="$NAVHOME/fastdds_nav.xml"

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
  # Standalone odom TF: started by navctl BEFORE the SLAM/Nav2 stack so the
  # C++ TF listeners inside slam_toolbox/Nav2 discover it (the client's
  # in-process relay is not reliably discovered by later C++ processes on
  # this Jetson). Republish the last /odom at 20 Hz so the TF survives when
  # the base is stationary.
  pgrep -f "webctl_odom_relay|webctl_navctl_odom" >/dev/null || \
  setsid bash -c "source $ROS_SRC && source $WS/install/setup.bash && exec python3 $NAVHOME/webctl_odom_relay.py webctl_navctl_odom" >>/tmp/nav_odom_tf.log 2>&1 </dev/null &
  # NOTE (2026-11-18): odom_combined->base_footprint now comes from
  # client.py's /odom relay. The separate robot_localization EKF cannot
  # discover the base /odom topic from a later DDS participant on this
  # Jetson (40% CPU, no TF, map_saver/service discovery equally broken).
  # Disable it so we never publish duplicate odom TF.
  pkill -f "webctl_navctl_ekf" 2>/dev/null
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
    # A live SLAM owns the helpers; don't churn them. If SLAM is down, stop
    # the Nav2 owner AND restart the helpers together with the SLAM — a new
    # SLAM does not reliably discover helper processes left over from the
    # previous navigation stack (map frame/pose stays None).
    if proc "[a]sync_slam_toolbox_node"; then echo "mapping already up"; exit 0; fi
    pkill -f 'bringup_launch.py' 2>/dev/null
    pkill -f 'component_container' 2>/dev/null
    pkill -f 'webctl_tf_repeater' 2>/dev/null
    pkill -f 'webctl_odom_relay' 2>/dev/null
    pkill -f 'webctl_scan_filter' 2>/dev/null
    sleep 2
    ensure_tf
    ensure_filter || true
    setsid bash -c "source $ROS_SRC && source $WS/install/setup.bash && exec ros2 run slam_toolbox async_slam_toolbox_node --ros-args --params-file $MAPPER --remap odom:=odom_combined" >/tmp/nav_map.log 2>&1 </dev/null &
    for t in $(seq 1 20); do sleep 1; proc "[a]sync_slam_toolbox_node" && break; done
    if proc "[a]sync_slam_toolbox_node"; then echo "mapping up"; else
      echo "FAILED: slam_toolbox died at startup — tail /tmp/nav_map.log"; exit 1; fi
    ;;
  map-save)
    name="${1:-room1}"
    # In nav-ready mode map_server runs inside the Nav2 component container,
    # not as a process literally named map_server.
    if ! proc "[a]sync_slam_toolbox_node" && ! proc "[m]ap_server" \
       && ! proc "[c]omponent_container" && ! proc "[b]ringup_launch.py"; then
      echo "FAILED: no live map publisher — start mapping or nav first"; exit 1
    fi
    # Humble's default save_map_timeout is 2.0 s. That is shorter than DDS
    # discovery + the next 1 Hz /map publish on this Jetson, which is why
    # map_saver_cli exited without files. Wait longer, force the transient-
    # local subscription, and retry. Also trust the exit code, so a stale
    # map from an earlier save is never reported as a fresh success.
    saved=0
    rc=1
    for attempt in 1 2 3; do
      ros2 run nav2_map_server map_saver_cli -f "$MAPS/$name" \
        --ros-args -p map_subscribe_transient_local:=true \
                   -p save_map_timeout:=15.0 >/tmp/nav_saver.log 2>&1
      rc=$?
      if [ "$rc" -eq 0 ] && [ -f "$MAPS/$name.pgm" ] && [ -f "$MAPS/$name.yaml" ]; then
        saved=1
        break
      fi
      sleep 2
    done
    if [ "$saved" -eq 1 ]; then
      echo "saved $MAPS/$name"
    else
      echo "FAILED[$NAVCTL_VERSION]: map_saver rc=$rc produced no files — $(tail -c 400 /tmp/nav_saver.log | tr '\n' ' ')"
      exit 1
    fi
    ;;
  stack-on)
    # Never stack a second Nav2 on top of a live one (repeated route/nav
    # presses used to create two containers and rejected goals). Restart the
    # helpers with the stack for the same discovery-order reason as map-on.
    pkill -f 'bringup_launch.py' 2>/dev/null
    pkill -f 'component_container' 2>/dev/null
    pkill -f 'webctl_tf_repeater' 2>/dev/null
    pkill -f 'webctl_odom_relay' 2>/dev/null
    pkill -f 'webctl_scan_filter' 2>/dev/null
    sleep 2
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
    pkill -f 'webctl_odom_relay'      2>/dev/null
    pkill -f 'webctl_navctl_ekf'     2>/dev/null
    pkill -f 'webctl_scan_filter'        2>/dev/null

    sleep 2
    pkill -9 -f 'bringup_launch.py'   2>/dev/null
    pkill -9 -f 'component_container' 2>/dev/null
    pkill -9 -f 'async_slam_toolbox_node' 2>/dev/null
    pkill -9 -f 'map_saver_cli'       2>/dev/null
    pkill -9 -f 'webctl_navctl_tf'    2>/dev/null
    pkill -9 -f 'webctl_odom_relay'   2>/dev/null
    pkill -9 -f 'webctl_navctl_ekf'   2>/dev/null
    pkill -9 -f 'webctl_scan_filter'       2>/dev/null

    sleep 2
    # NEVER delete /dev/shm/fastrtps_*: those segments belong to the base
    # driver, the robotctl client and the scan filter as well. Wiping them
    # breaks DDS discovery for every still-running process — the "new
    # process sees no graph / services unreachable / latched map missing"
    # failures. FastDDS cleans up its own segments on exit.
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
