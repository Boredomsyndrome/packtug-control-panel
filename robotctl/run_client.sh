#!/usr/bin/env bash
# Run the robot client with ZERO install steps on the robot:
#   * pure-stdlib python3 client (no pip, no apt, internet never required)
#   * env from ~/.config/wheeltec-robotctl.env (or first CLI arg)
#   * runs in your shell / tmux / desktop autostart / (optionally) systemd
#
# Deliberately NO `set -eu` here: ROS/colcon setup.bash scripts reference
# possibly-unbound vars ($COLCON_PREFIX_PATH) and may return nonzero. Under
# `set -u`/`set -e`, SOURCING them kills this shell before python ever
# starts — the exact silent-death bug this launcher used to have (the
# "unbound variable" error even went to /dev/null). A launcher that
# disappears without a message is worse than no launcher.
DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ---- env file ----
ENV_FILE="${1:-${WEBCTL_ENV_FILE:-$HOME/.config/wheeltec-robotctl.env}}"
if [ -f "$ENV_FILE" ]; then
  echo "robotctl: using $ENV_FILE"
  set -a
  # tr -d '\r': env files edited on Windows / copied via USB carry CRLF,
  # which silently poisons values like "http://...:8000\r".
  . <(tr -d '\r' <"$ENV_FILE") || echo "robotctl: WARNING: env file failed to source" >&2
  set +a
else
  echo "robotctl: no env file at $ENV_FILE — copy robotctl/robotctl.env.example"
  echo "          there (chmod 600) and fill in SERVER/ID/TOKEN. Trying anyway."
fi

# ---- ROS environment: rclpy/cv2 come from here (nothing is installed) ----
[ -f "$DIR/backend/requirements.txt" ] && \
  echo "robotctl: (note: backend/requirements.txt is for the SERVER host, not the robot)"
for f in /opt/ros/humble/setup.bash "$HOME/wheeltec_robot/install/setup.bash"; do
  if [ -f "$f" ]; then
    # shellcheck disable=SC1090
    . "$f"
    rc=$?
    [ $rc -eq 0 ] || echo "robotctl: note: sourcing $f returned rc=$rc (continuing; base ROS may still be OK)" >&2
  fi
done
command -v ros2 >/dev/null 2>&1 || \
  echo "robotctl: note: ros2 CLI not found — if the client logs 'rclpy missing', check $HOME/wheeltec_robot/install/setup.bash exists and source the right ROS setup" >&2

# ---- exec the client (this line printing = wrapper survived) ----
PYBIN="$(command -v python3)"
if [ -z "$PYBIN" ]; then
  echo "robotctl: FATAL: no python3 on PATH (impossible on stock Jetson image — check PATH)" >&2
  exit 1
fi
echo "robotctl: exec $PYBIN -u $DIR/robotctl/client.py"
exec "$PYBIN" -u "$DIR/robotctl/client.py"
