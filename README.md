# PackTug Control Panel

Operator control panel for **PackTug — an AIoT-enabled smart roll-cage system** (DASE 4135, Systems Integration).

The system automates cold-chain warehouse picking, roll-cage packing and cross-floor transfer by coordinating an AI 3D bin-packing engine, a mobile AGV + robotic-arm unit (a **WHEELTEC S200** platform towing roll cages) and goods-lift scheduling.

This repository is the operator panel. It is a **hybrid prototype**: orders, packing, missions and cages use local mock data, while the Fleet page reads live S200 status from the teammate’s pull-mode Django server, and `/arm` drives a live Niryo Ned1 through the FastAPI bridge.

---

## Status

| Area | State |
| --- | --- |
| Orders, packing, missions, zones, commands, dashboard | Implemented on mock data |
| 3D bin-packing engine | Client prototype (`ExtremePoint-FirstFit`) |
| AGV server (`wheeltec-webctl/`, Django ASGI) | **Live** pull-mode server on `:8090`; teammate console handles driving |
| AGV telemetry | **Live** online / battery / transport via `/api/vehicle`, plus LiDAR `/scan`; pose and map still pending |
| Robotic arm | **Live** Niryo **Ned1** via `server/arm_server.py` (1–20% speed, ≤60° per command) |
| Vision / QR | Manual ROI detection and fixed-camera calibration tools; eye-in-hand calibration in progress |

The panel shows explicit **MOCK MODE** / **hardware offline** indicators per surface so mock state is never mistaken for live data.

---

## Features

- **Dashboard** — KPI cards (active orders, cages in use, fleet online, average utilisation), throughput and cage-utilisation charts, live mission list and activity feed.
- **Orders / Order detail** — create orders, per-SKU volume & weight roll-up, trigger packing, assign an order to an AGV.
- **3D Packing** — computes cage count, utilisation and per-carton placement coordinates; renders the packed cage with a dependency-free isometric canvas viewer (rotate / spin / per-item highlight).
- **Missions** — pick / pack / transport / charge missions with step timelines, progress and pause / resume / abort.
- **Fleet & Cages** — live S200 online/battery/transport status, a link to the teammate’s AGV console, plus mock cage and last-known platform records. A warehouse-map placeholder is shown until the scenario map is modelled.
- **Zones** — cold store / staging / dispatch / charging zones with temperature and capacity, plus a "re-optimise" action.
- **Commands** — high-level operator commands: *load order X onto AGV Y* and *re-optimise packing of zone Z*.
- **Arm Control** — live Ned1 joint terminal: connect by WiFi/ethernet, drag joint sliders, nudge in 1/2/5/10° steps, apply absolute targets, adjust the speed cap (1–20%) and max gripper-tip reach, choose linear Cartesian or joint paths, open/close the gripper, release motors for manual posing, auto-calibrate, save/recall/delete pose presets, and build/run pose sequences.
- **Integration** — interface inventory with live arm/camera/vehicle endpoints, plus the remaining mock and planned surfaces.
- **Login** — lightweight role-based access (operator / supervisor / maintenance).

---

## Tech stack

- [Vite 5](https://vitejs.dev/) + [React 18](https://react.dev/) + [TypeScript 5](https://www.typescriptlang.org/)
- [React Router 6](https://reactrouter.com/) for routing
- [Recharts 2](https://recharts.org/) for charts
- Custom `<canvas>` isometric renderer for the 3D cage view (no 3D library)
- No runtime dependency on any backend in mock mode

---

## Getting started

Requires Node.js 18+.

```bash
npm install
npm run dev        # http://localhost:5173/
```

Other scripts:

```bash
npm run build      # type-check (tsc -b) + production build to dist/
npm run preview    # serve the production build
npm run typecheck  # type-check only
```

---

## Arm control terminal (Niryo Ned1)

The `/arm` page talks to `server/arm_server.py`, a small FastAPI bridge that
wraps PyNiryo 1.1.2 in the `ned` conda env. It also serves the built panel, so
no separate Vite server is needed for the demo.

```bash
cd control-panel
conda run --no-capture-output -n ned python server/arm_server.py --port 8020
# UI only, no arm attached:
conda run --no-capture-output -n ned python server/arm_server.py --mock
```

Open `http://127.0.0.1:8020/arm`. Connection addresses: WiFi hotspot
`10.10.10.10`, direct Ethernet `169.254.200.200`, and Jetson relay
`127.0.0.1` (requires the local SSH tunnel to port `40001`).

Safety: the bridge caps the arm at **1–20% speed** (default 5%), accepts at most
**60° per command**, without internal 15° subdivision. This kit has **no
hardware E-stop**, so keep the workspace clear and a hand on the power switch.
Dragging a joint slider commits when you release it. **Manual pose** releases the
motors — hold the arm before enabling it, then press **Lock position** to
re-engage.

### Collision faults and recovery

Before each move the bridge remembers the current joints; every successful 60°
segment updates that **last safe point**. After a move (or a linear recall) it
also probes the robot's `GET_COLLISION_DETECTED` flag when the firmware supports
it. A collision/overload, a robot `KO`, or a reported hardware error latches a
motion fault and blocks further commands; the page shows the failing joint
position and whether a safe retract point is available.

**Recover to last safe point** runs a slow (2%) reverse move to that last
confirmed-safe joint position. It deliberately does **not** run an automatic
`home` sweep, which could drag the arm through the obstacle. If the reverse move
also fails, the fault stays latched — release the motors, move the arm clear by
hand, then use **Clear after inspection**. This gives you a one-click recovery
for the common "arm bumped an object and stopped crooked" case without
re-teaching or recalibrating.

Joint limits follow the robot's runtime MoveIt limits. The J5 upper limit was
opened from +55° to **+110°** in both the robot-side URDF and the bridge (0.5°
safety margin), after confirming the physical unit can hold J5 above 55°.
Existing saved poses outside the limits are clamped when loaded.

If the arm reports `need_calibration`, tick the confirmation and press
**Initialize / auto-calibrate** in the Connection card (slow full-range sweep,
~30 s), or run `../arm/ned_calibrate.py --go`. The gripper buttons appear once
the tool scan detects a gripper; use **Rescan tool** after changing tools.

**Pose presets** save the current six joint angles to
`server/arm_poses.json` under a name. Recall moves the arm back with the current
speed cap, in up to 60° segments, and allows up to 180° per joint. Saving while motors
are released captures the hand-posed position. The list shows each pose's
gripper-tip horizontal reach.

Pose/sequence recall uses a **linear Cartesian path** by default; this avoids
the outward arc produced by joint-space interpolation between far-apart poses.
The **Max reach** setting (measured at the gripper tip, assuming a 0.10 m tool
extension) is enforced on every move: joint-space paths that bulge beyond it are
rejected with the offending segment/radius, while linear paths check the
endpoints. Turn off *Linear path* to use the older joint-space behaviour.

**Sequences** chain saved poses A→B→C with a dwell time and are stored in
`server/arm_sequences.json`. They run in the browser using the current
linear/joint path setting: each pose is applied in order, and **Stop sequence**
takes effect after the current blocking pose finishes. Closing the Arm page also
stops the sequence after the current pose.

An optional **Transit** is applied before every step; choose a saved raised
pose or the built-in **Home / fold**. This avoids sweeping low across the table
between far-apart corners. A direct joint-space sweep with the arm extended can
make the robot return a `KO`, which the bridge reports as HTTP 502 (the terminal
log now shows the failing segment and joint angles, and pose recalls prefix the
error with the pose name).

`.env` is set to `VITE_MOCK=false` so the arm page uses the bridge. The Fleet
page reads live AGV status from the pull-mode server; its cage/platform records
remain mock. Other pages remain on mock data.

### Camera stream (Jetson + RealSense)

The arm page also shows a live RGB feed. The camera runs on the Jetson, not on
the control laptop. It is installed as the enabled systemd service
`packtug-camera.service`, so it starts automatically at boot and restarts if it
exits. The unit and installer are in `deploy/`:

```bash
# on the Jetson, from the repo checkout
sudo ./deploy/setup_camera_service.sh
sudo ./deploy/setup_camera_watchdog.sh   # restarts the bridge if frames freeze
sudo ./deploy/setup_arm_link.sh eth0     # 169.254.200.1/16, 100 Mb/s full
```

If `lsusb` has no `Intel ... Depth Camera 455` and the log says
`No RealSense devices were found`, the D455 USB link is physically disconnected;
reseat it before expecting camera frames.

The bridge owns `realsense2_camera` and can switch between RGB and depth from
the page:

```bash
# on the Jetson (manual fallback; the service normally runs this)
systemctl status packtug-camera.service
source /opt/ros/humble/setup.bash
python3 ~/camera_stream.py --manage-camera --mode rgb --port 8080
```

The manager starts the camera as `camera_name:=d455` (`/d455/color/image_raw`
and `/d455/depth/image_raw`) so it does not collide with the Orbbec navigation
camera on `/camera`. Do not start a second `realsense2_camera` node. The RGB /
Depth buttons call `/api/camera/mode`; switching restarts the camera and takes
about 10 seconds. Depth is currently unavailable on the D455's USB2 link and
needs a USB3 host.

The FastAPI bridge proxies the streams through `/api/camera/*`. Point it at the
Jetson (Tailscale IP or LAN IP):

```bash
export CAMERA_STREAM_URL=http://100.73.98.69:8080/stream
export CAMERA_DEPTH_URL=http://100.73.98.69:8080/depth
export CAMERA_MODE_URL=http://100.73.98.69:8080/mode
conda run --no-capture-output -n ned python server/arm_server.py --port 8020
```

`--port 8080` is on the robot side; `8080` is also the usual `web_video_server`
port. The browser only talks to the control panel origin, so this works over
Tailscale without mixed-content/CORS issues. The MJPEG proxy must preserve the
upstream `Content-Type` boundary (`multipart/x-mixed-replace; boundary=frame`).

## AGV pull-mode console

The S200 is **not** a push/rosbridge server in the current integration. A
`robotctl` process on the Jetson dials out to the Django ASGI hub on the Mac, so
the robot exposes no control ports. The teammate’s console at `:8090` is the
driving UI; this panel links to it from **Fleet** and proxies only status via
`GET /api/vehicle`. LiDAR is enabled separately with
`WEBCTL_SCAN_MODE=auto`; the client subscribes to `/scan` and the console draws
the returned scan.

The nested repo is `wheeltec-webctl/` (its own git checkout; gitignored here).
Create `wheeltec-webctl/.env` from `.env.example`, set the per-robot token, and
**do not commit it**. Then start the hub:

```bash
cd wheeltec-webctl
nohup bash scripts/run_server.sh 0.0.0.0 8090 >/tmp/wheeltec-webctl.log 2>&1 &
```

On the Jetson, start the outbound client:

```bash
nohup setsid bash /home/wheeltec/robotctl/run_client.sh \
  >/tmp/robotctl.log 2>&1 </dev/null &
```

Both `~/.config/wheeltec-robotctl.env` and `~/robotctl/robotctl.env` must use
the current hub URL, `WEBCTL_ROBOT_ID=wheeltec`, the matching token, and
`WEBCTL_NAV=1`. A stale `s2000-1`/old-LAN env will hold `.robotctl.lock`; the
console then shows OFFLINE and all drive buttons stay disabled even though the
server and page are up.

Open `http://<Mac-host>:8090/`. The browser API is network-trusted (Tailscale or
trusted LAN) and has no separate browser password; do not expose it publicly.
Server access logs can contain the robot token in WebSocket query strings, so
do not paste raw logs.

## Vision / calibration

`/arm` includes an RGB stream, manual QR ROI detection, and a fixed-camera
6-point pixel→arm mapping. The camera is being moved to the arm, so the saved
fixed-camera mapping is only a development aid. The last 6-point fit was about
19.7 mm RMSE and is **not safe for autonomous motion**; eye-in-hand
(`T_tool_camera`) calibration and a pick/place state machine are still pending.
A test lift was aborted by the robot with a collision/motor-trajectory error.

## Five-box demo

`ORD-STACK-5` / `DEMO-BOX` is a fixed demo plan: four 152×85×110 mm boxes in a
2×2 floor grid plus one centered on the back row. See `docs/stack-demo-5.md`
for cage-frame coordinates and gripper targets. It is special-cased in
`src/data/demoStack.ts`, not produced by the normal packer.

## Project structure

```
control-panel/
├── server/
│   ├── arm_server.py           # FastAPI: Ned1, vision, camera, vehicle proxy
│   ├── camera_stream.py        # Jetson RGB/depth MJPEG bridge
│   └── vision_calibration.json # saved fixed-camera mapping (development only)
├── src/
│   ├── App.tsx                 # routes + mock-auth gate
│   ├── main.tsx
│   ├── index.css               # design system (dark industrial theme)
│   ├── types.ts                # domain model (Order, Cage, Agv, Mission, PackingPlan…)
│   ├── components/
│   │   ├── Cage3DView.tsx      # rAF canvas renderer for a packed cage
│   │   ├── Layout.tsx          # sidebar + topbar shell
│   │   ├── status.tsx          # status → badge colour mappings
│   │   └── ui.tsx              # Card / Badge / Progress / KpiCard / PageHeader
│   ├── data/
│   │   ├── mock.ts             # SKUs, orders, cages, AGVs, zones, missions, throughput
│   │   └── demoStack.ts        # fixed five-box stack demo
│   ├── lib/
│   │   ├── packing.ts          # extreme-point first-fit 3D bin packing
│   │   └── format.ts           # formatting helpers
│   ├── pages/                  # Dashboard, Orders, OrderDetail, Packing, Missions,
│   │                           # Fleet, Zones, Commands, Arm, Integration, Login
│   ├── services/
│   │   ├── api.ts              # mock-backed panel commands and integration status
│   │   ├── endpoints.ts        # endpoint inventory
│   │   └── vehicle.ts          # live pull-mode AGV status client
│   └── store/
│       └── AppStore.tsx        # app state + software-only simulation loop
├── docs/                       # stack demo, paper block, calibration assets
└── wheeltec-webctl/            # teammate pull-mode repo (nested git; gitignored)
```

---

## Integration surfaces

- **Live**: `/api/arm/*`, `/api/camera/*`, `/api/vision/*` via `server/arm_server.py`; `/api/vehicle` proxies the pull-mode Django hub. `/arm` and the Fleet live-status card use these.
- **Mock**: orders, packing, missions, zones, commands, dashboard, cages and auth. `src/services/api.ts` still returns mock data; `src/data/mock.ts` is the source.
- **`src/services/endpoints.ts`** — interface inventory:
  - Panel REST: `/api/agvs`, `/api/cages`, `/api/zones`, `/api/orders`, `/api/orders/:id/pack`, `/api/orders/:id/dispatch`, `/api/missions/:id/control`, `/api/zones/:id/reoptimise`, `/api/metrics/throughput`, `/api/auth/login`
  - Pull-mode AGV: `/api/vehicle` (status), teammate console at `:8090` (driving)
  - Planned telemetry: `/ws/telemetry` (AGV pose / cage / mission / metrics stream)
  - ROS 2 bridge topics: `/cmd_vel`, `/odom`, `/battery_state`, `/imu/data_raw`, `/scan`, `/mission/command`, `/mission/feedback`
- **`src/services/vehicle.ts`** — read-only live fleet status client. Driving is intentionally delegated to the teammate console; do not add a second drive pad here.
- **`src/lib/packing.ts`** — client prototype packer. In production the server-side [`skjolber/3d-bin-container-packing`](https://github.com/skjolber/3d-bin-container-packing) library (strategies `LAFF` and brute-force) takes over behind `POST /api/orders/:id/pack`.

### Switching off mock mode

Create a `.env` file:

```bash
VITE_MOCK=false
VITE_API_BASE=/api        # or the deployed backend URL
```

---

## Robot platform notes (WHEELTEC S200)

The S200 runs ROS 2 Humble. Relevant defaults and bringup commands:

```bash
# bring up base + sensors on the robot
ros2 launch turn_on_wheeltec_robot turn_on_wheeltec_robot.launch.py
ros2 launch turn_on_wheeltec_robot wheeltec_sensors.launch.py

# navigation
ros2 launch wheeltec_nav2 wheeltec_nav2.launch.py

# verify telemetry
ros2 topic echo /battery_state
ros2 topic echo /odom
```

| Setting | Value |
| --- | --- |
| SSH user | `wheeltec` (password: team vault only, never commit) |
| Robot IP | `192.168.0.100` |
| Hotspot | `WHEELTEC_ORINNANO_HUMBLE` |

---

## Roadmap

- [ ] Import the modelled test-scenario map (`map.pgm` + `map.yaml`) and overlay AGV pose, zones and paths
- [ ] Wire `src/services/api.ts` to the Django REST backend
- [ ] Replace the client packer with the server-side `3d-bin-container-packing` engine
- [ ] AGV pose/map telemetry in the panel (battery/status is already live)
- [ ] Hand-eye calibration and a safe arm pick/place state machine
- [ ] Weight / centre-of-gravity stability validation in the packing plan
- [ ] Export packing plans (JSON) for the robotic-arm executor
