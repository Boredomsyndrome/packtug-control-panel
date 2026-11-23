#!/usr/bin/env bash
# cpu_recon.sh — READ-ONLY CPU recon for the S2000 Jetson (Orin Nano).
# Run ON the robot after power-up, ideally while mapping (navctl map-on) so
# we measure the hog under load. No state changes, no restarts. stdlib only.
# Decision tree: plans/2026-11-20-jetson-cpu-budget.md
echo "== power preset (sudo pw: ~/.config/wheeltec-sudo-pass via stdin) =="
echo "$(< ~/.config/wheeltec-sudo-pass)" | sudo -S -p '' nvpmodel -q 2>/dev/null | grep -E "NV Power|MODE" || echo "nvpmodel unavailable"
echo "-- current freqs (kHz) --"
for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq; do cat "$f" 2>/dev/null; done | sort -n | uniq -c
echo "-- governor --"; cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null

echo; echo "== top by %CPU (5s sample) =="
top -b -d 5 -n 1 | awk 'NR==1||/CPU|%Cpu|MiB/{if(NR<8)print} NR>6 && $1 ~ /^[0-9]+$/ {print}' | sort -k9 -rn | head -20

echo; echo "== topic rates (12s each; run during map-on for load) =="
source /opt/ros/humble/setup.bash 2>/dev/null
timeout 14 ros2 topic hz --window 30 /scan 2>/dev/null | tail -2
timeout 14 ros2 topic hz --window 30 /scan_filtered 2>/dev/null | tail -2

echo; echo "== per-node cpu (lifetime-avg pcpu, % of ONE core) =="
probe() { # $1 label, $2 pgrep pattern
  local pid; pid=$(pgrep -f "$2" | head -1)
  [ -z "$pid" ] && { printf "%-26s NOT RUNNING\n" "$1"; return; }
  printf "%-26s pid %-7s %s\n" "$1" "$pid" "$(ps -o pcpu=,pmem=,cputime=,etime= -p "$pid" | tr -s ' ')"
}
probe slam-toolbox "[a]sync_slam_toolbox_node"
probe scan-filter "[w]ebctl_scan_filter"
probe ekf "[w]ebctl_navctl_ekf|[r]obot_localization"
probe rplidar "[r]plidar|[s]llidar"
probe webctl-client "[r]un_client|client[.]py"
probe navd "[n]avd[.]py"
probe amcl "[c]arto_amcl|[a]mcl"
probe nav2 "[c]ontroller_server|[p]lanner_server|[b]ehavior_server"

echo; echo "== thermal (millideg C) =="
for z in /sys/class/thermal/thermal_zone*/type; do echo "$(cat "$z"): $(cat "${z%/type}/temp")"; done 2>/dev/null
echo DONE
