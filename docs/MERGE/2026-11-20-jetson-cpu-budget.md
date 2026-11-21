# Jetson CPU budget: can we rewrite the nav/LiDAR path in C/C++?

**Question (Comet 2026-11-20):** navigation LiDAR already consumes 100% of a single core — rewrite in C or C++?
**Short answer: the nav hot path is ALREADY ~95% C++. The consumed core belongs to `async_slam_toolbox_node`, which is C++ with a Ceres solver backend — there is nothing to rewrite there. The win is in OUR OWN aggressive mapper params + Orin power preset + optional affinity.**

## Hardware
Jetson Orin Nano Dev Kit (tegra234, P3767-0004 → 4 GB variant), 6× Cortex-A78AE. NOTE: Orin Nano dev kits ship with **NVP Model power presets (7 W / 15 W / 25 W-MAXN)** — a 7 W/15 W preset caps A78 clocks hard; "100% of one core" on a capped clock is partially a *frequency* problem, not just a code problem. `sudo nvpmodel -q` + `sudo jetson_clocks` are the free CPU you may already be paying for and not using.

## Language audit of the nav hot path (robot-side, verified from repo + vendor notes)
| Component | Language | Hot-path cost | Rewrite value |
| --- | --- | --- | --- |
| `async_slam_toolbox_node` | **C++** (upstream slam_toolbox, Ceres SPARSE_NORMAL_CHOLESKY) | THE core consumer | ❌ already C++ |
| Nav2 (AMCL, controller, costmaps) | C++ | only during route | ❌ |
| `robot_localization` EKF | C++ | ~1–3% | ❌ |
| vendor rplidar driver node | C++ (vendor install) | serial + publish, low | ❌ |
| `webctl_scan_filter.py` | rclpy + **numpy vectorized** box mask | ~3–8% est. at 10 Hz | ⚠️ possible but pointless unless measured >15% |
| `client.py` / `navd.py` | stdlib Python, 10 Hz JSON + occasional CLI | ~2–4% | ❌ (network plane, not LiDAR plane) |

**So the premise "rewrite in C/C++" is already satisfied everywhere it matters.**

## rclpy-vs-rclcpp research (what a Python→C++ port would actually buy)
- robotics.stackexchange #98772 + ros2/rclpy#763: pre-Galactic "30–100×" horror story for big payloads — **fixed in Galactic** (Humble has the fix).
- sbel.wisc.edu TR-2023-03 (measured on a Jetson Orin, Humble): rclpy publish ≈ **6×** rclcpp, latency ≈ **3×** — for large messages; small 10 Hz scans are in the noise.
- rclcppyy benchmarks (cppyy C++ executor backend): 4–6× CPU saving **at 1–3 kHz / MB-scale payloads** — our scan filter is 10 Hz × ~360–800 floats → rclpy cost is single-digit % of one A78 core.
→ Porting the filter node to C++ recovers maybe 0.05 core at best. **Not the missing core.**

## Where the missing core actually is: OUR mapper param profile (self-inflicted)
`robotctl/nav_params/mapper_rplidar_a1.yaml` deliberately maxed for responsiveness (comments show we tuned DOWN from defaults):
| Param | Ours | Upstream default | CPU effect |
| --- | --- | --- | --- |
| `throttle_scans` | **1** (match every scan) | 1 (but 2–4 is the documented embedded lever) | matcher+optimizer cost ∝ scan rate |
| `minimum_time_interval` | **0.0** ("process immediately") | 0.5 | removes all back-pressure |
| `map_update_interval` | **1.0 s** | 5.0 | rasterization + serialization 5× |
| `max_laser_range` | 10.0 m | 12 | ray march cost |
| `correlation_search_space` | 0.3 m @ 0.01 | 0.5 @ 0.01 | search grid ≈ 31³ per matched scan |
| `do_loop_closing` | **true** @ 8 m @ 0.05 res | true | the classic Jetson hog (161² grid + smear) |
| `enable_interactive_mode` | **true** (never used by webctl) | true | keeps graph data alive, more bookkeeping |

## Ranked levers (cheap → exotic)
1. **Power preset check** — `sudo nvpmodel -q`; 25 W/MAXN + `sudo jetson_clocks` (persist: rc.local or systemd unit) → may *double effective throughput* with zero code change.
2. **Low-CPU mapper profile** (prepared, NOT applied): `robotctl/nav_params/mapper_rplidar_a1_lowcpu.yaml` — throttle_scans 3, minimum_time_interval 0.2, map_update_interval 5.0, max_laser_range 5.0, correlation res 0.02, loop closure thresholds raised + loop res 0.10, interactive off, transform_publish_period 0.05, tf_buffer 30. Expected 2–4× slam cost at walking speeds. Re-wire cost: ONE line in navctl.sh (`MAPPER=`), deliberately left untouched.
3. **Scan decimation in the filter** (numpy, one line): matcher cost is linear in ray count; A1 at 360 rays → keep 120–180. Trade: slightly coarser wall edges. Fold into low-CPU rollout as option.
4. **Affinity split** (`taskset -c 2-5` on slam/nav2 group, leave 0–1 for webctl client + EKF) — protects the 10 Hz ws telemetry + estop latency from the slam core war. Experiment, document-only for now.
5. **C++ laser_filters plugin** for the box mask (pluginlib, ~150 ln; loses odom-lag-learner stamping unless ported) — only if recon shows the python filter >15% of a core. Cheap insurance, keep in pocket.
6. **FASTRTPS SHM re-enable** — real CPU saver (skips UDP ser/deser + copies) but re-opens our documented DDS-island wound: safe ONLY if the profile is sourced by EVERY ROS process including vendor boot units (lidar/base nodes we don't launch). High risk, last resort.
7. rclcppyy / alternative runtimes: no — solves a problem we don't have (kHz payloads).

## Measurement kit (robot OFFLINE — run first when powered up)
`tools/cpu_recon.sh` (read-only, stdlib-only): nvpmodel + live cpufreq, top snapshot by %CPU, `ros2 topic hz /scan /scan_filtered`, per-pid CPU (slam/filter/EKF/lidar/client), thermal zones. Decision tree:
- slam >90% at 25 W → lever 2+3, expect 15–40% residual.
- filter node >15% → lever 5 becomes real.
- clocks pinned <1.3 GHz under load → lever 1 was the real answer.
- lidar/base node hogging → vendor problem, pin/renice.

## Status
- [x] Examined ROS2 libraries: slam_toolbox/Nav2/EKF/rplidar = already C++; only numpy filter + stdlib client are Python, and benchmarks prove they're not the hog.
- [x] Candidate low-CPU profile + recon script written locally.
- [ ] NOTHING applied to robot or navctl.sh (per standing "do not do it yet"). Rollout order after Comet's go: nvpmodel check → deploy recon → run recon mapping pass → apply low-CPU profile via MAPPER line → re-measure → decide affinity.
