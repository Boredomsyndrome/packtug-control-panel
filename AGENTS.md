# AGENTS.md — PackTug control-panel

`control-panel/` is a separate git repo from the parent course folder and from `wheeltec-webctl`. The parent `../AGENTS.md` is stale on the robot model and limits; use this file plus the live hardware.

## Commands

- `npm run typecheck` — TypeScript only; no ESLint or test suite.
- `npm run build` — `tsc -b && vite build`; this is the frontend verification. Built files go to `dist/`.
- `npm run dev` — Vite on `:5173`, proxying `/api` to `127.0.0.1:8020`.
- On this Mac, Node/npm live in `/usr/local/bin`; if `npm` is not found:
  `PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin npm run build`.
- FastAPI bridge (serves `dist` plus `/api/arm/*`, `/api/camera/*`, `/api/vehicle/*`):
  `/opt/anaconda3/bin/conda run --no-capture-output -n ned python server/arm_server.py --port 8020`
  Add `--mock` when no arm is attached. `conda` is not always on the tool PATH; use `/opt/anaconda3/bin/conda` or a login shell.
- Use conda env `ned` (Python 3.11, `pyniryo==1.1.2`). Never install pyniryo into `wheeltec`.
- Python syntax check: `python3 -m py_compile server/arm_server.py server/camera_stream.py`.
- Restart `arm_server.py` after Python changes; rebuild `dist` after frontend changes. There is no backend reloader.
- Restart `8020` by the actual listener, not `pgrep` alone:
  `lsof -tiTCP:8020 -sTCP:LISTEN | xargs kill -9`, confirm the port is free, then start a fresh server. A conda wrapper can hide a stale child.
- `arm_server.py` reads `CAMERA_*` and `WHEELTEC_WEBCTL_URL` directly; it does not load `.env`. Export env vars before starting it. Frontend `.env` only controls Vite (`VITE_MOCK`, `VITE_API_BASE`).
- Teammate pull-mode AGV server (separate nested repo):
  `cd wheeltec-webctl && nohup bash scripts/run_server.sh 0.0.0.0 8090 >/tmp/wheeltec-webctl.log 2>&1 &`
  It requires `wheeltec-webctl/.venv` and its local `.env`; bind `0.0.0.0:8090` or the robot cannot dial it over Tailscale.
- Do not commit/push unless asked; the working tree is normally dirty. `wheeltec-webctl/` has its own `.git` and is gitignored by this repo.

## Current control path

- The control panel normally runs on the Mac at `127.0.0.1:8020`; the browser is local.
- Arm traffic goes: Mac → Tailscale → Jetson `sshd` → UGREEN USB Ethernet → arm.
  Start the local forward in a terminal:
  `ssh -N -L 40001:169.254.200.200:40001 wheeltec@100.73.98.69`
- The UI connection preset **Jetson relay · 127.0.0.1** uses this tunnel. The **Ethernet** preset is for an arm server running on the Jetson (or direct arm Ethernet).
- Before restarting `arm_server.py`, call `POST /api/arm/disconnect` or it may leave a stale arm TCP session. `HostNotReachableException` after a restart usually means the arm service has a stale session; power-cycle the arm controller if a normal reconnect does not clear it.
- If the arm link stays up but ping loss/`HostNotReachableException` returns, reset the NetworkManager connection (`nmcli con down arm-link && nmcli con up arm-link`). The `arm-link` profile is 169.254.200.1/16 on eth0 and autoconnects.
- The UGREEN/arm link must be **100 Mb/s full duplex**, not 1 Gb/s autoneg. At 1 Gb/s it showed 50-75% packet loss and `HostNotReachableException`; forcing 100 Mb/s restored 0% loss and a stable PyNiryo session. Use `deploy/setup_arm_link.sh`; do not toggle EEE as a fix (it made the link worse).
- Tailscale is currently Mac `100.86.19.104` and Jetson `100.73.98.69`; it may use a DERP relay instead of a direct path.

## Fleet / AGV integration

- `wheeltec-webctl/` is the teammate’s **pull-mode** control repo, nested under `control-panel/` as a separate git checkout. Do not confuse it with the old `/Users/wuliao/Desktop/wheeltec-webctl` rosbridge/push snapshot.
- The live AGV UI is the teammate’s console at `http://<Mac-host>:8090/`, linked from our Fleet page as **Open AGV control**. Do not re-add our custom `LiveVehicle` drive pad; that component and the `/api/vehicle/*/cmd|stop|estop` proxy were removed. `/api/vehicle` is Fleet status only.
- Architecture: browser/django hub on the Mac (`:8090`) + `robotctl/client.py` on the Jetson dialing **out**; the robot exposes no ports and uses WebSocket with HTTP long-poll fallback.
- Robot registry and clamps live in `wheeltec-webctl/.env` (`WEBCTL_ROBOTS=wheeltec:<token>`, currently `MAX_LIN=0.4`, `MAX_ROT=0.7`). The matching robot env is `/home/wheeltec/.config/wheeltec-robotctl.env` with `WEBCTL_SERVER=http://100.86.19.104:8090`. Never print, commit, or paste the token. Rotate it if it appears in logs.
- The robot client is `/home/wheeltec/robotctl/run_client.sh` (copy of `wheeltec-webctl/robotctl`); it uses the Jetson ROS environment and publishes `/cmd_vel`. Camera upload is disabled (`WEBCTL_CAM_MODE=off`); LiDAR upload is enabled (`WEBCTL_SCAN_MODE=auto`) and subscribes to `/scan` at ~12 Hz.
- Start it detached:
  `ssh wheeltec@100.73.98.69 'nohup setsid bash /home/wheeltec/robotctl/run_client.sh >/tmp/robotctl.log 2>&1 </dev/null &'`.
  Avoid a `pkill -f /home/wheeltec/robotctl/client.py` in the same remote command: the pattern can match the SSH shell itself.
- The Jetson has **two** robotctl env files: `~/.config/wheeltec-robotctl.env` (used by `run_client.sh`) and `~/robotctl/robotctl.env` (used by `stack_up.sh` / `watchdog_vpn.sh`). Both must have `WEBCTL_SERVER=http://100.86.19.104:8090`, `WEBCTL_ROBOT_ID=wheeltec`, the same token as the hub, and `WEBCTL_NAV=1`. A stale `s2000-1` / old `192.168.*` env holds `.robotctl.lock`; the console stays OFFLINE and drive buttons stay disabled, and a second client logs `another instance holds the lock`. Fix both files, kill the exact old client PID, then restart `run_client.sh`.
- `watchdog_vpn.sh` parses `WEBCTL_SERVER` from the old env every 2 min and can bounce the client; keep that line valid or the watchdog will treat the hub as unreachable even when the UI is otherwise connected.
- Only one `scripts/run_server.sh` may own the hub (flock); a second process returns 503. The teammate integration test is
  `wheeltec-webctl/.venv/bin/python -u tools/integration_test.py` (uses fake ports 8100/8111/9103).
- The teammate browser/API has **no browser password**; access control is network trust (Tailscale/trusted LAN) plus the per-robot token. Do not expose `:8090` publicly.
- The ASGI access log can print the robot token from the WebSocket query string. Do not paste raw server logs into chat/commits; rotate `WEBCTL_ROBOTS` if a token is exposed.
- The old `wheeltec-webctl/backend/robot/bridge.py` push/rosbridge code is not the active path. Use the pull-mode `robotctl` + Django ASGI server.

## Arm / robot

- Actual arm is a **Ned (Ned1)**, not Ned2; older tooling/docs used the wrong name. Use the live `hardware_version`/joint readings as truth.
- Direct Ethernet address `169.254.200.200`; PyNiryo TCP `40001`; one TCP client at a time. Do not run a second arm server while port `8020` owns it.
- The arm is now connected to the Jetson through a UGREEN RTL8153 USB-Ethernet adapter. The adapter appears as `eth0` or `eth1` depending on boot order; verify with `lsusb` and `ip -br link`. Give it `169.254.200.1/16` and keep it in the same link-local L2 as the arm.
- Tool detection can transiently fail with error `-251 Impossible to set new tool`; retrying **Rescan tool** often restores `GRIPPER_1`. Normal and raw gripper commands are blocked while `tool` is null.
- `POST /api/arm/pose` now exposes a linear Cartesian pose move. It is not a complete autonomous pick sequence; do not treat it as one.
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
- The arm link through the UGREEN RTL8153 is a persistent NetworkManager profile named `arm-link` (169.254.200.1/16, 100 Mb/s full duplex, autoconnect). Install/repair it with `sudo deploy/setup_arm_link.sh eth0` (run on the Jetson; the repo copy is the source). Confirm with `ip -br link`; the interface may swap between `eth0` and `eth1`.
- Camera bridge runs on the Jetson, not the Mac, as the enabled systemd service **`packtug-camera.service`** (D455 RGB, port 8080; auto-starts at boot and restarts on failure). The unit is in this repo at `deploy/packtug-camera.service`; install with `sudo deploy/setup_camera_service.sh`. Check with `systemctl status packtug-camera.service`; manual fallback:
  `source /opt/ros/humble/setup.bash && python3 ~/camera_stream.py --manage-camera --mode rgb --port 8080`.
  It owns the `realsense2_camera` child process; do not also launch a separate realsense node.
- The service can stay `active` while its frame stream is frozen. Check `curl http://127.0.0.1:8080/health`: if `color.age_sec` grows beyond a few seconds, restart `packtug-camera.service`; QR/vision will otherwise keep detecting on a stale snapshot. A pause/resume of the realsense child can leave it in this state. `deploy/setup_camera_watchdog.sh` installs a 30 s timer that restarts a stale bridge.
- If `/health` has `color.age_sec: null` and the journal says `No RealSense devices were found`, the D455 USB is unplugged or not enumerated; restarting the service will not help. Reseat the camera; `lsusb` must show `ID 8086:0b5c`.
- D455 is on USB2 (`480M`); the Jetson USB3 root (`Bus 002`, 10000M) has no devices and no accessible USB3 port has been found. Do not claim USB3.
- D455 RGB works at `640x480@15`; D455 depth does **not** produce frames over this USB2 link. The earlier depth data on `/camera` came from the **Orbbec Astra S**, which is used by the S200 navigation stack — do not kill or reconfigure it.
- D455 uses namespace `camera_name:=d455` and topics `/d455/color/...`, `/d455/depth/...` to avoid colliding with Orbbec on `/camera`.
- Mode switch (`/api/camera/mode?value=rgb|depth`) restarts the camera and takes ~10 s. RGB is the working mode; Depth will be blank until a USB3 host or D405 is available.
- `camera_stream.py` also serves color/depth MJPEG and `depth_stats`; the FastAPI proxy must preserve the upstream `Content-Type` including `boundary=frame`, otherwise the browser shows live but no image.
- Start/stop the Jetson bridge with `fuser -k 8080/tcp` or an anchored kill; do not leave an old `camera_stream.py` holding port 8080.
- Camera URLs default to `http://100.73.98.69:8080/{stream,depth,mode}` via `CAMERA_STREAM_URL`, `CAMERA_DEPTH_URL`, `CAMERA_MODE_URL`; override if the Tailscale IP changes. `camera_stream.py` also serves `/snapshot`, which the QR endpoint uses.
- The camera is moving from the temporary cart/tape view to a rigid **arm/wrist mount** (eye-in-hand). Once it is on the arm, the existing fixed-camera 6-point pixel→arm calibration is invalid. Use camera intrinsics + `T_tool_camera` hand-eye calibration, then combine with the current arm pose. Keep the mount rigid and synchronize capture with a stationary arm pose.
- D455 may be disconnected independently of the Jetson; camera loss must not be presented as an arm/base fault.

## Vision / QR

- The current block has a small QR tag. Full-frame QR detection is unreliable at 640x480; use the manual click or a tight dragged box in the RGB view.
- `POST /api/vision/qr` accepts `x,y,radius` or explicit `roi_x,roi_y,roi_w,roi_h`. ROI detection upscales/thresholds the crop; a tight box around the QR works best. The tag needs roughly 50+ px and must not be occluded by the hand/gripper.
- UI: in the Camera card, click the QR or drag a box around it; the overlay shows the center/corners/angle. The **Vision calibration** card records `(QR pixel → current arm x/y)` **only for a fixed camera**.
- Calibration endpoints: `GET/POST/DELETE /api/vision/calibration`, `POST /api/vision/calibration/point`, `POST /api/vision/calibration/solve`, and `POST /api/vision/map`.
- Fixed-camera calibration requires **6 2D-spread points**, not 4. A 4-point fit with RMSE 0 is mathematically meaningless; the backend rejects points with less than 80 px x-spread, 20 px y-spread, or 40 mm arm x/y spread.
- The last saved fixed-camera 6-point calibration is **not usable**: it solved with roughly **19.7 mm RMSE**. Do not use it for autonomous motion, and do not reuse it after the camera is mounted on the arm. For eye-in-hand, calibrate `T_tool_camera` with a fixed ChArUco board over 10–15 arm poses.
- No autonomous pick state machine exists yet. `/api/arm/pose` can move linearly, but a test lift was aborted by the robot with a collision/motor-trajectory error. Do not claim automatic pick/place is working.
- Do **not** trust the first calibration of a session: verify the residual with at least 6 points and keep the workspace clear.

## Five-box demo

- `ORD-STACK-5` / `DEMO-BOX` is a fixed paper-box demo, **not** produced by the normal packer. `src/data/demoStack.ts` special-cases it in `AppStore.runPacking`.
- Current layout: four `152 x 85 x 110 mm` boxes on the floor in a 2x2 grid plus one on the back row; `docs/stack-demo-5.md` has the cage coordinates and gripper targets. The back row is intentionally near the cage wall for stability; the top box spans the 9 mm gap at 94% support.
- The demo plan is illustrative; the physical arm still needs eye-in-hand calibration and a pick/place state machine.

## Verification

- Frontend: `npm run typecheck && npm run build`.
- Backend: `python3 -m py_compile ...`, then start the FastAPI server and exercise `GET /api/health`, `GET /api/arm/status`, `GET /api/camera/stream`, and `GET /api/vehicle` (proxy to the pull-mode server).
- AGV: `GET http://127.0.0.1:8090/api/robot` should report `wheeltec` online/transport `ws`; the console is at `http://<Mac-host>:8090/`.
- Vision: click/drag near the QR, then check `POST /api/vision/qr`; use `GET /api/vision/calibration` and `POST /api/vision/map` only while the camera is fixed.
- Hardware verification is manual; there is no automated arm, vision, or camera test suite. Always keep the workspace clear and a hand on the power switch.
