#!/usr/bin/env bash
# deploy_to_robot.sh — the ONE sanctioned way to put code on the robot.
# Everything flows: repo (git) -> potatoserver checkout -> scp -> robot.
# Never edit robot files by hand; edit the repo, commit, run this.
#
#   tools/deploy_to_robot.sh [user@host]   (default wheeltec@192.168.42.11)
#
# Copies ONLY versioned files (client.py, stack_up.sh), never the robot's
# robotctl.env (holds its token — lives on the robot only), then runs the
# idempotent bring-up. Key-auth assumed (ssh-copy-id'd from potatoserver).
set -euo pipefail
ROBOT=${1:-wheeltec@192.168.42.11}
cd "$(dirname "$0")/.."
git diff --quiet || echo "WARN: working tree dirty — deploying MODIFIED files (commit first!)"

for f in robotctl/client.py deploy/stack_up.sh deploy/watchdog_lidar.sh; do
  base=$(basename "$f")
  local_sha=$(sha256sum "$f" | cut -d' ' -f1)
  remote_sha=$(ssh "$ROBOT" "sha256sum ~/robotctl/$base 2>/dev/null | cut -d' ' -f1" || true)
  if [ "$local_sha" = "$remote_sha" ]; then
    echo "sync: $base already matches repo"
  else
    scp -q "$f" "$ROBOT:~/robotctl/$base"
    echo "copied: $base -> $ROBOT (restart below)"
  fi
done

ssh "$ROBOT" 'chmod +x ~/robotctl/stack_up.sh && exec bash ~/robotctl/stack_up.sh'
