#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"

sudo install -m 644 "$DIR/packtug-camera.service" /etc/systemd/system/packtug-camera.service
sudo systemctl daemon-reload
sudo systemctl enable --now packtug-camera.service
