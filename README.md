# PackTug Control Panel — fused operator site

Single site to control the whole PackTug system: **orders / packing /
missions** (mock business layer), the **Niryo Ned1 arm + vision** plane, and
the **WHEELTEC S2000 AGV** (teleop, LiDAR, SLAM mapping, waypoint routes).
Everything server-side is **one FastAPI app**; the old Django plane is gone
for good (v3.8+). Merged history: `packtug-control-panel` + `wheeltec-webctl`
v3.6.6 (`docs/MERGE/`).

## Layout — exactly two running trees

```
backend/     THE SERVER (one uvicorn process, one port)
  webctl/asgi.py      raw-ASGI entrypoint + ws routing + hub lifespan
  webctl/config.py    env registry (WEBCTL_ROBOTS, clamps) — pure Python
  robot/              robot plane: api/hub/ws/navstore/validators
  arm_server.py       arm/vision bridge (FastAPI, mounted in-process)
  camera pose JSONs   arm_poses.json · arm_sequences.json
  run_server.sh       launcher (flock = single owner)

robot/       THE ROBOT (ships as a unit to ~/robotctl/ on the Jetson)
  start-all.sh        ONE command = every robot function (see below)
  client.py           stdlib-only hub uplink: telemetry+scan+cam+cmd+navd
  navd.py navctl.sh nav_params/ ekf_navctl.yaml   autonomy (opt-in)
  webctl_scan_filter.py webctl_tf_repeater.py     scan mask + TF bridge
  camera_stream.py    D455 RGB bridge :8080 (+ systemd unit & installers)
  watchdog_lidar.sh watchdog_vpn.sh wifi_join.sh  resilience
  robotctl.env.example requirements.txt (zero-dep signpost)
```

Everything else: `src/` + `index.html` = panel SPA (React+TS, builds to
`dist/` + the AGV drive console inlined as a page (`src/pages/agv/`, route
`/drive` — the standalone `agv-console/` SPA merged into the panel), `docs/` guides + API +
legacy/MERGE archives, `tools/` deploy + test harnesses (server-host side).
HTTPS front: see `docs/ACCESS.md` (Caddy lives outside this repo).

## Architecture (one server, one port, one websocket to the robot)

```
 browser ─────────────── http://<hub>:8090 ────────────────────────┐
   │ /            panel SPA (React+TS) incl. AGV console (/drive) │
   │ /api/robot*  /ws/cam (browser viewer)                         │
   │ /api/arm* /api/camera* /api/vision* /api/vehicle* /api/health │
   ▼                                                               │
 ONE uvicorn process :8090  (flock = single owner, all FastAPI)    │
   ├─ robot plane (backend/robot) — hub mailbox, estop, clamps,    │
   │   maps, scan ingest, panel SPA static serving                 │
   └─ arm bridge (backend/arm_server.py create_app, mounted        │
      in-process for its /api/* prefixes — mock or real Ned1)      │
        ▲                                                          │
        │ ONE websocket  /ws/robotctl/<id>?token=  per robot       │
        ▼ (outbound from robot; HTTP long-poll fallback)
 robot/client.py  — single python script on the Jetson:
   telemetry + LiDAR /scan + camera JPEG + cmd consume + navd
   (map/nav/route) all ride that ONE websocket. Zero deps, nothing
   else installed, robot exposes no ports.
```

Safety model unchanged (see `docs/API.md`): server clamps, staleness
zeroing, latched estop, onboard client watchdog. Access control = network
trust (LAN/VPN only, never internet-exposed) + per-robot token.

## Run the server

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
npm ci && npm run build                     # panel SPA -> dist/ (incl. AGV console)
cp .env.example .env                        # WEBCTL_ROBOTS=id:token
set -a; . ./.env; bash backend/run_server.sh 0.0.0.0 8090
```

Containerized server: `docker compose up -d --build` — runbook `docker.md`.
The robot side is never containerized (it needs native ROS).

## Run the robot

One folder, one command. `tools/deploy_robot.py` (paramiko) ships the whole
`robot/` tree to `~/robotctl/` on the Jetson and merges the env (token stays
on the robot). Then, on the robot:

```bash
bash ~/robotctl/start-all.sh     # base + lidar + ASTRA + client(+navd),
                                 # plus status of camera service & watchdogs
```

`start-all.sh` is idempotent; boot persistence via crontab `@reboot`
(header of the file has the line). Root-owned extras — D455 camera service,
lidar/VPN watchdogs, arm-link, WiFi STA mode — have one-time installers in
the same folder (`setup_camera_service.sh`, `setup_arm_link.sh`, …).

## Guides

* `SETUP.md`, `ROBOT_SETUP.md`, `SCHOOL_SETUP.md` — deployment walkthroughs.
* `docs/API.md` — every endpoint + wire frames. `docs/NAV.md` — autonomy.
* `AGENTS.md` — arm/vision operational notes (arm traffic, UGREEN link).
* `robot/README.md` — what each robot file does.
* `docs/MERGE/2026-11-21-MERGE-COMPLETION-STD.md` — what "done" means.

## Verification gates

| gate | command |
| --- | --- |
| ROS shim suite | `.venv/bin/python -u tools/test_ros_shims.py` |
| hub integration (76+ checks) | `.venv/bin/python -u tools/integration_test.py` |
| prod launcher smoke | `bash tools/prod_smoke_test.sh` |
| bridge tests | `.venv/bin/python -m pytest tests -q` |
| frontend logic tests | `node --test tests/*.test.mjs` |
| navctl param render sim | `bash tools/sim_navctl_render.sh` |
| panel + drive console build | `npm run build` at repo root |
