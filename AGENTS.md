# AGENTS.md — PackTug control-panel

`control-panel/` is a separate git repo from the parent course folder and from `wheeltec-webctl`. The parent `../AGENTS.md` is stale on the robot model and limits; use this file plus the live hardware.

## Commands

- `npm run typecheck` — TypeScript only; no ESLint or test suite.
- `npm run build` — `tsc -b && vite build`; this is the frontend verification. Built files go to `dist/`.
- `npm run dev` — Vite on `:5173`, proxying `/api` to `127.0.0.1:8020`.
- FastAPI bridge (serves `dist` plus `/api/arm/*` and `/api/camera/*`):
  `conda run --no-capture-output -n ned python server/arm_server.py --port 8020`
  Add `--mock` when no arm is attached.
- Use conda env `ned` (Python 3.11, `pyniryo==1.1.2`). Never install pyniryo into `wheeltec`.
- Python syntax check: `python3 -m py_compile server/arm_server.py server/camera_stream.py`.
- Restart `arm_server.py` after Python changes; rebuild `dist` after frontend changes. There is no backend reloader.
- `arm_server.py` reads `CAMERA_*` environment variables directly; it does not load `.env`. Export env vars before starting it. Frontend `.env` only controls Vite (`VITE_MOCK`, `VITE_API_BASE`).
- Do not commit/push unless asked; the working tree is normally dirty.

## Arm / robot

- Actual arm is a **Ned (Ned1)**, not Ned2, despite the README/docs. Use the live `hardware_version`/joint readings as truth.
- Direct Ethernet address `169.254.200.200`; PyNiryo TCP `40001`; one TCP client at a time. Do not run a second arm server while port `8020` owns it.
- No hardware E-stop. Speed cap is **20%** (`MAX_VELOCITY_LIMIT`); J1 is a stepper with high reduction and is normally slower than the wrist.
- `MAX_STEP_DEG` was raised to **60°** (the old 15° subdivision is gone); manual commands remain capped at 60° per joint.
- J5 was opened to **+110°** in the bridge and robot-side xacro; robot-side backup: `~/catkin_ws/src/niryo_robot_description/urdf/ned/niryo_ned_param.urdf.xacro.bak-j5-110`. A robot service restart requires recalibration.
- J6 is an XL-320 and is prone to jitter; do not mount the camera or extra payload on J6.
- Collision/overload/`KO` latches a fault; use **Recover to last safe point**, which retracts at 2% to the last confirmed joints. There is no automatic home sweep.
- Saved data is user data: `server/arm_poses.json` and `server/arm_sequences.json`; do not wipe.
- The coordinate-input autonomous path planner is **not implemented**; do not present it as working.

## Jetson / camera

- The S200 SBC is a **Jetson Orin Nano**, host `wheeltec`, Ubuntu 22.04 `tegra` + ROS 2 Humble. Current Tailscale node: `100.73.98.69` (verify with `tailscale status`; DHCP can change).
- HKU `Wi-Fi.HK via HKU` has client isolation; use Tailscale, not direct SSH over that SSID.
- Default WHEELTEC credentials/AP are in `../WHEELTEC ROS2 S200 Brushless Mobile Robot.pdf`: AP `WHEELTEC_ORINNANO_HUMBLE`, robot `192.168.0.100`. Do not put passwords in this repo.
- Camera bridge runs on the Jetson, not the Mac:
  `source /opt/ros/humble/setup.bash && python3 ~/camera_stream.py --manage-camera --mode rgb --port 8080`
  It owns the `realsense2_camera` child process; do not also launch a separate realsense node.
- D455 is on USB2 (`480M`); the Jetson USB3 root (`Bus 002`, 10000M) has no devices and no accessible USB3 port has been found. Do not claim USB3.
- D455 RGB works at `640x480@15`; D455 depth does **not** produce frames over this USB2 link. The earlier depth data on `/camera` came from the **Orbbec Astra S**, which is used by the S200 navigation stack — do not kill or reconfigure it.
- D455 uses namespace `camera_name:=d455` and topics `/d455/color/...`, `/d455/depth/...` to avoid colliding with Orbbec on `/camera`.
- Mode switch (`/api/camera/mode?value=rgb|depth`) restarts the camera and takes ~10 s. RGB is the working mode; Depth will be blank until a USB3 host or D405 is available.
- `camera_stream.py` also serves color/depth MJPEG and `depth_stats`; the FastAPI proxy must preserve the upstream `Content-Type` including `boundary=frame`, otherwise the browser shows live but no image.
- Start/stop the Jetson bridge with `fuser -k 8080/tcp` or an anchored kill; do not leave an old `camera_stream.py` holding port 8080.
- Camera URLs default to `http://100.73.98.69:8080/{stream,depth,mode}` via `CAMERA_STREAM_URL`, `CAMERA_DEPTH_URL`, `CAMERA_MODE_URL`; override if the Tailscale IP changes.

## Verification

- Frontend: `npm run typecheck && npm run build`.
- Backend: `python3 -m py_compile ...`, then start the FastAPI server and exercise `GET /api/health`, `GET /api/arm/status`, and `GET /api/camera/stream`.
- Hardware verification is manual; there is no automated arm or camera test suite.
