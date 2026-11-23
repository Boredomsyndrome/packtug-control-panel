#!/usr/bin/env bash
# v3.7 CS-B5: fake-$HOME trap sim for navctl.sh's param-copy render.
# Builds a mini vendor param file in a temp HOME, runs `navctl.sh status`
# (which triggers the render), and asserts both injections landed.
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/robotctl" \
         "$TMP/wheeltec_ros2/src/wheeltec_robot_nav2/param/wheeltec_param"
cat > "$TMP/wheeltec_ros2/src/wheeltec_robot_nav2/param/wheeltec_param/param_four_wheel_diff_bs.yaml" <<'EOF'
amcl:
  ros__parameters:
    scan_topic: scan
local_costmap:
  observables:
    observation_sources: scan
  scan:
          topic: /scan
bt_navigator:
  ros__parameters:
    plugin: Nav2Backup
EOF
HOME="$TMP" bash "$REPO/robot/navctl.sh" status >/dev/null 2>&1 || true
OUT="$TMP/robotctl/nav_params/param_four_wheel_diff_bs.webctl.yaml"
fail=0
[ -f "$OUT" ] || { echo "FAIL: param copy not rendered"; exit 1; }
grep -q "scan_topic: /scan_filtered" "$OUT" || { echo "FAIL: amcl scan remap"; fail=1; }
grep -q "topic: /scan_filtered" "$OUT" || { echo "FAIL: costmap topic remap"; fail=1; }
grep -q "default_nav_to_pose_bt_xml" "$OUT" || { echo "FAIL: BT injection"; fail=1; }
grep -q "\$HOME_ISH" /dev/null || true   # noop anchor
grep -q "navigate_no_spin_backup.xml" "$OUT" || { echo "FAIL: BT path"; fail=1; }
# vendor source file must be untouched
grep -q "scan_topic: scan" "$TMP/wheeltec_ros2/src/wheeltec_robot_nav2/param/wheeltec_param/param_four_wheel_diff_bs.yaml" \
  || { echo "FAIL: vendor file modified!"; fail=1; }
[ "$fail" -eq 0 ] && echo "NAVCTL RENDER SIM: ALL CHECKS PASSED"
exit "$fail"
