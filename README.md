# PackTug Control Panel

Operator control panel for **PackTug — an AIoT-enabled smart roll-cage system** (DASE 4135, Systems Integration).

The system automates cold-chain warehouse picking, roll-cage packing and cross-floor transfer by coordinating an AI 3D bin-packing engine, a mobile AGV + robotic-arm unit (a **WHEELTEC S200** platform towing roll cages) and goods-lift scheduling.

This repository is the **front-end prototype**. It runs entirely on mock data so the full operator workflow can be demonstrated before any hardware is delivered. Every point where the real backend or robot must be plugged in is isolated behind a reserved interface layer.

---

## Status

| Area | State |
| --- | --- |
| Orders, packing, missions, zones, commands, dashboard | Implemented on mock data |
| 3D bin-packing engine | Client prototype (`ExtremePoint-FirstFit`) |
| Backend (Django REST + PostgreSQL) | **Reserved** — not connected |
| ROS 2 bridge (Jetson ↔ backend) | **Reserved** — offline |
| WHEELTEC S200 telemetry (pose, battery) | **Disabled** — awaiting the modelled test map + hardware |
| Niryo Ned2 arm control | **Live** via `server/arm_server.py` (1–20% speed, ≤60° per command) |

The panel shows explicit **MOCK MODE** / **hardware offline** indicators so mock state is never mistaken for live data.

---

## Features

- **Dashboard** — KPI cards (active orders, cages in use, fleet online, average utilisation), throughput and cage-utilisation charts, live mission list and activity feed.
- **Orders / Order detail** — create orders, per-SKU volume & weight roll-up, trigger packing, assign an order to an AGV.
- **3D Packing** — computes cage count, utilisation and per-carton placement coordinates; renders the packed cage with a dependency-free isometric canvas viewer (rotate / spin / per-item highlight).
- **Missions** — pick / pack / transport / charge missions with step timelines, progress and pause / resume / abort.
- **Fleet & Cages** — S200 platform records and roll-cage fill level, weight and centre of gravity. A warehouse-map placeholder is shown until the scenario map is modelled.
- **Zones** — cold store / staging / dispatch / charging zones with temperature and capacity, plus a "re-optimise" action.
- **Commands** — high-level operator commands: *load order X onto AGV Y* and *re-optimise packing of zone Z*.
- **Arm Control** — live Ned2 joint terminal: connect by WiFi/ethernet, drag joint sliders, nudge in 1/2/5/10° steps, apply absolute targets, adjust the speed cap (1–20%) and max gripper-tip reach, choose linear Cartesian or joint paths, open/close the gripper, release motors for manual posing, auto-calibrate, save/recall/delete pose presets, and build/run pose sequences.
- **Integration** — live view of the reserved REST/WS/ROS 2 interface surface.
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

## Arm control terminal (Niryo Ned2)

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
`10.10.10.10`, direct Ethernet `169.254.200.200`.

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

`.env` is set to `VITE_MOCK=false` so the arm page uses the bridge; other
pages remain on mock AGV data.

### Camera stream (Jetson + RealSense)

The arm page also shows a live RGB feed. The camera runs on the Jetson, not on
the control laptop. The bridge can own `realsense2_camera` and switch between
RGB and depth from the page:

```bash
# on the Jetson
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

## Project structure

```
control-panel/
├── index.html
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
│   │   └── mock.ts             # SKUs, orders, cages, AGVs, zones, missions, throughput
│   ├── lib/
│   │   ├── packing.ts          # extreme-point first-fit 3D bin packing
│   │   └── format.ts           # formatting helpers
│   ├── pages/                  # Dashboard, Orders, OrderDetail, Packing, Missions,
│   │                           # Fleet, Zones, Commands, Integration, Login
│   ├── services/
│   │   ├── api.ts              # reserved command layer (mock-backed)
│   │   └── endpoints.ts        # documented REST / WS / ROS 2 interface surface
│   └── store/
│       └── AppStore.tsx        # app state + software-only simulation loop
```

---

## Reserved interfaces (hardware / backend hooks)

The prototype is fully functional without a backend, but the integration boundary is explicit and stable:

- **`src/services/endpoints.ts`** — documents every endpoint the panel will consume:
  - REST: `/api/agvs`, `/api/cages`, `/api/zones`, `/api/orders`, `/api/orders/:id/pack`, `/api/orders/:id/dispatch`, `/api/missions/:id/control`, `/api/zones/:id/reoptimise`, `/api/metrics/throughput`, `/api/auth/login`
  - WebSocket: `/ws/telemetry` (AGV / cage / mission / metrics stream)
  - ROS 2 bridge topics: `/cmd_vel`, `/odom`, `/battery_state`, `/imu/data_raw`, `/scan`, `/mission/command`, `/mission/feedback`
- **`src/services/api.ts`** — all commands flow through this module. Each function currently returns mock data; replace the `TODO` bodies with `fetch()` calls to go live.
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
| Default user | `wheeltec` / `dongguan` |
| Robot IP | `192.168.0.100` |
| Hotspot | `WHEELTEC_ORINNANO_HUMBLE` |

---

## Roadmap

- [ ] Import the modelled test-scenario map (`map.pgm` + `map.yaml`) and overlay AGV pose, zones and paths
- [ ] Wire `src/services/api.ts` to the Django REST backend
- [ ] Replace the client packer with the server-side `3d-bin-container-packing` engine
- [ ] Real ROS 2 telemetry via `/ws/telemetry`
- [ ] Weight / centre-of-gravity stability validation in the packing plan
- [ ] Export packing plans (JSON) for the robotic-arm executor
