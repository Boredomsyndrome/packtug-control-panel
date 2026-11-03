#!/usr/bin/env bash
# Run the robot client with ZERO install steps on the robot:
#   * pure-stdlib python3 client (no pip, no apt, internet never required)
#   * env from ~/.config/wheeltec-robotctl.env (or first CLI arg)
#   * runs in your shell / tmux / desktop autostart / (optionally) systemd
set -eu
DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${1:-$HOME/.config/wheeltec-robotctl.env}"
if [ -f "$ENV_FILE" ]; then
  set -a; . "$ENV_FILE"; set +a
  echo "robotctl: using $ENV_FILE"
else
  echo "robotctl: no env file at $ENV_FILE — copy robotctl/robotctl.env.example"
  echo "          there (chmod 600) and fill in SERVER/ID/TOKEN. Trying anyway."
fi
# rclpy/cv2 ship in the WHEELTEC image; they import when ROS is sourced.
# (Sourcing is env-only — nothing is installed.)
[ -f /opt/ros/humble/setup.bash ] && . /opt/ros/humble/setup.bash 2>/dev/null || true
[ -f "$HOME/wheeltec_robot/install/setup.bash" ] && . "$HOME/wheeltec_robot/install/setup.bash" 2>/dev/null || true
exec python3 "$DIR/robotctl/client.py"
