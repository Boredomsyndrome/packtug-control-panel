# PackTug Control Panel — fused operator site

Single site to control the whole PackTug system: **orders / packing /
missions** (mock business layer), the **Niryo Ned1 arm + vision** plane, and
the **WHEELTEC S2000 AGV** (teleop, LiDAR, SLAM mapping, waypoint routes).

Merged 2026-11-21 from `packtug-control-panel` @ `ca265cc` (tag
`v3.6.6-fusion-base`) + `wheeltec-webctl` v3.6.6 @ `876e044` (tag
`v3.6.6-premerge-ours`, merged in with full history). Merge plan +
completion standard live in `docs/MERGE/` (was
`~/wheeltec-webctl/.hermes/plans/`).

## Architecture (one server, one port, one websocket to the robot)

```
 browser ─────────────── http://<hub>:8090 ────────────────────────┐
   │ /            panel SPA (React+TS)  ·  /agv/  AGV console     │
   │ /api/robot*  /ws/cam (browser viewer)                         │
   │ /api/arm* /api/camera* /api/vision* /api/vehicle* /api/health │
   ▼                                                               │
 ONE uvicorn process :8090  (flock = single owner)                 │
   ├─ Django ASGI  (robot/nav/fleet plane: hub mailbox, estop,     │
   │                clamps, maps, scan ingest, /agv/ + panel SPA)  │
   └─ FastAPI arm bridge (server/arm_server.py create_app, mounted │
      in-process for its /api/* prefixes — mock or real Ned1)      │
        ▲                                                          │
        │ ONE websocket  /ws/robotctl/<id>?token=  per robot       │
        ▼ (outbound from robot; HTTP long-poll fallback)
 robotctl/client.py  — single python script on the Jetson:
   telemetry + LiDAR /scan + camera JPEG + cmd consume + navd
   (map/nav/route) all ride that ONE websocket. Zero deps, nothing
   else installed, robot exposes no ports.
```

Safety model unchanged (see `docs/API.md`): server clamps, staleness
zeroing, latched estop, onboard client watchdog. Access control = network
trust (LAN/VPN only, never internet-exposed) + per-robot token.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
cd frontend/panel && npm ci && npm run build          # site root '/'
cd ../agv-console && npm ci && npm run build          # served at '/agv/'
cp .env.example .env                                  # WEBCTL_ROBOTS=id:token
.venv/bin/python -u tools/integration_test.py         # gates, see docs/MERGE/
set -a; . ./.env; bash scripts/run_server.sh 0.0.0.0 8090
```

Robot side: `tools/deploy_robot.py` (paramiko) ships `robotctl/` to the
Jetson; the robot runs one script —
`bash run_client.sh ~/robotctl/robotctl.env`.

* `SETUP.md`, `ROBOT_SETUP.md`, `SCHOOL_SETUP.md` — robot deployment guides.
* `docs/API.md` — every endpoint + wire frames. `docs/NAV.md` — autonomy.
* `AGENTS.md` — arm/vision operational notes (arm traffic, UGREEN link).
* `docs/MERGE/2026-11-21-MERGE-COMPLETION-STD.md` — what "done" means.
* `deploy/nav/` — generated copies of the robot nav plane; source of
  truth = `robotctl/` + `deploy/` in this repo.

## Verification gates

| gate | command |
| --- | --- |
| ROS shim suite | `.venv/bin/python -u tools/test_ros_shims.py` |
| hub integration (76+ checks) | `.venv/bin/python -u tools/integration_test.py` |
| prod launcher smoke | `bash tools/prod_smoke_test.sh` |
| bridge tests | `.venv/bin/python -m pytest tests -q && node tests/digitalTwin.test.mjs` |
| panel + console build | `npm run build` in `frontend/panel` and `frontend/agv-console` |
