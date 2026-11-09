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
MAPPER="$WS/src/wheeltec_robot_slam/wheeltec_slam_toolbox/config/mapper_params_online_async.yaml"

cmd="$1"
[ $# -gt 0 ] && shift

source "$ROS_SRC" 2>/dev/null
[ -f "$WS/install/setup.bash" ] && source "$WS/install/setup.bash" 2>/dev/null
mkdir -p "$MAPS"

has_topic() { timeout 6 ros2 topic list 2>/dev/null | grep -qx "$1"; }
has_topic_like() { timeout 6 ros2 topic list 2>/dev/null | grep -q "$1"; }

case "$cmd" in
  map-on)
    if has_topic /map; then echo "mapping already up (/map live)"; exit 0; fi
    setsid bash -c "source $ROS_SRC && source $WS/install/setup.bash && exec ros2 run slam_toolbox async_slam_toolbox_node --ros-args -p params_file:=$MAPPER" >/tmp/nav_map.log 2>&1 </dev/null &
    sleep 3
    if has_topic /map; then echo "mapping up"; else
      echo "FAILED: slam_toolbox did not publish /map — tail /tmp/nav_map.log"; exit 1; fi
    ;;
  map-save)
    name="${1:-room1}"
    has_topic /map || { echo "FAILED: no /map — start mapping first"; exit 1; }
    ros2 run nav2_map_server map_saver_cli -f "$MAPS/$name" >/tmp/nav_saver.log 2>&1
    if [ -f "$MAPS/$name.pgm" ] && [ -f "$MAPS/$name.yaml" ]; then
      echo "saved $MAPS/$name"
    else
      echo "FAILED: map_saver produced no files — tail /tmp/nav_saver.log"; exit 1
    fi
    ;;
  stack-on)
    name="${1:-room1}"
    [ -f "$MAPS/$name.yaml" ] || { echo "FAILED: map '$name' not saved"; exit 1; }
    # slam_toolbox and AMCL both want to own map->odom: hand the TF over.
    pkill -f async_slam_toolbox_node 2>/dev/null
    sleep 2
    setsid bash -c "source $ROS_SRC && source $WS/install/setup.bash && exec ros2 launch wheeltec_nav2 bringup_launch.py slam:=false autostart:=true map:=$MAPS/$name.yaml params_file:=$PARAM" >/tmp/nav_stack.log 2>&1 </dev/null &
    sleep 10
    if has_topic /amcl_pose || has_topic /initialpose; then
      if has_topic_like follow_waypoints; then echo "nav up (map=$name, waypoints ok)"
      else echo "nav up (map=$name) WARN: /follow_waypoints not visible yet"; fi
    else
      echo "FAILED: AMCL not live — tail /tmp/nav_stack.log"; exit 1
    fi
    ;;
  stop)
    pkill -f 'bringup_launch.py'      2>/dev/null
    pkill -f 'component_container'    2>/dev/null
    pkill -f 'async_slam_toolbox_node' 2>/dev/null
    pkill -f 'map_saver_cli'          2>/dev/null
    sleep 2
    pkill -9 -f 'bringup_launch.py'   2>/dev/null
    pkill -9 -f 'component_container' 2>/dev/null
    pkill -9 -f 'async_slam_toolbox_node' 2>/dev/null
    pkill -9 -f 'map_saver_cli'       2>/dev/null
    sleep 1
    if has_topic /amcl_pose || has_topic /map; then
      echo "WARN: nav topics still live after stop"; exit 1
    fi
    echo "nav stopped"
    ;;
  status)
    state=""
    has_topic /map && state="$state mapping"
    has_topic /amcl_pose && state="$state localizing"
    has_topic_like follow_waypoints && state="$state waypoints"
    [ -n "$state" ] && { echo "running$state"; exit 0; }
    echo "idle"; exit 0
    ;;
  *)
    echo "usage: navctl.sh map-on|map-save <name>|stack-on <name>|stop|status"
    exit 2
    ;;
esac
