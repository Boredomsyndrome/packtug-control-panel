#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"

sudo install -m 755 "$DIR/packtug-camera-watchdog" /usr/local/sbin/packtug-camera-watchdog
sudo install -m 644 "$DIR/packtug-camera-watchdog.service" /etc/systemd/system/packtug-camera-watchdog.service
sudo install -m 644 "$DIR/packtug-camera-watchdog.timer" /etc/systemd/system/packtug-camera-watchdog.timer
sudo systemctl daemon-reload
sudo systemctl enable --now packtug-camera-watchdog.timer
