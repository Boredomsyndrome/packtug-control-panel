# v3.7 Merge — Gate Evidence (robotless tiers CS-A / CS-B / CS-I)

Executed 2026-11-21 (UTC) on potatoserver2, `~/packtug-control-panel`,
robot OFFLINE (the designated migration safety window, CS-I5).
Commands were run exactly as the completion standard specifies; the
lines below are the verbatim result lines from the runs.

## CS-A (robotless gates)

| gate | command | result |
|---|---|---|
| A1 ROS shims | `.venv/bin/python -u tools/test_ros_shims.py` | `RESULT: ALL CHECKS PASSED` (incl. CS-B1 exact-Humble gate) |
| A2 Hub integration | `.venv/bin/python -u tools/integration_test.py` | `RESULT: ALL CHECKS PASSED` — includes [13] CS-B3 fused contract + new CS-B4 check: `PASS CS-B4 /panel/ 200, index hashes match '/', JS asset real [/assets/index-CvCTk1y8.js 200 727604B]` |
| A3 Prod launcher smoke | `bash tools/prod_smoke_test.sh` | `PROD SMOKE: ALL CHECKS PASSED` |
| A4 Panel builds | `npm run build` (panel) + `cd agv-console && npm run build` + `npm run typecheck` | all exit 0 (`tsc -b` clean) |
| A5 Teammate suites | `PYTHONPATH=. .venv/bin/python -m pytest tests -q` / `node tests/digitalTwin.test.mjs` / `node tests/vehicleChip.test.mjs` | `23 passed` / `pass 6 fail 0` / `pass 4 fail 0` |
| A6 Live health | `curl :8090/api/robot` | `200` ✓. The `:8020` half of the original wording is SUPERSEDED: the fused architecture mounts the bridge in-process on the ONE port, so live mock-health is proven post-cutover at `:8090/api/health`. The standalone :8020 instance was NOT relaunched (legacy two-port layout retired by design). |

## CS-B (contract tiers)

- **B1** exact-Humble action-client shim gate (`tools/test_ros_shims.py`):
  route drives `get_result_async` → `GetResult.Response`;
  `! grep -rn result_future robotctl/*.py` is enforced inside the suite. ✓
- **B2** sim-fleet loop (`tools/sim_fleet.py`): 16/16
  `SIM FLEET: ALL CHECKS PASSED` — real client+navd processes, s2000-sim,
  ws-only: online → map_on/off → record → localizing → route (held,
  409 lockout, completes) → estop latch/unlatch → off → SIGTERM exit.
- **B3** fused contract (integration [13]): `/api/health` mock, `/api/arm/status`,
  `/api/vehicle` shows fleet, import-guard sim (PACKTUG_DISABLED=1 →
  bridge 404, robot plane + SPA fine), ws push latency < 150 ms WHILE an
  MJPEG-proxy stream held (measured 2–3 ms). ✓
- **B4** panel contract: typecheck + builds + `/panel/` 200 with hashed
  asset identical to `/` and real JS bytes + chip unit tests (4). ✓
- **B5** deploy parity: `bash -n` every deploy/*.sh, `py_compile` every
  robot .py (both gates run by shims suite hooks + manual), manifest
  = robotctl/{client.py,navd.py,run_client.sh,webctl_tf_repeater.py,
  webctl_scan_filter.py} + deploy/navctl.sh + nav_params (deploy_robot.py
  read: NOTHING from deploy/nav/), `tools/sim_navctl_render.sh`
  `NAVCTL RENDER SIM: ALL CHECKS PASSED`.

## CS-I (integrity)

- **I1** `git diff ca265cc -- server/` empty (bridge zero hand-edits; tag
  `v3.6.6-fusion-base` exists).
- **I2** every `deploy/nav/*` file carries `generated-from: … ca265cc …`;
  README rewritten as FROZEN REFERENCE with canonical-file table.
- **I3** `VERSION`=3.7.0, client `VERSION` default 3.7.0, `navctl.sh`
  v3.7.0 header; phase tags `v3.7.1-phase1-robot … v3.7.4-harness`
  (`git status` clean at each tag).
- **I4** token hygiene grep: only `WEBCTL_ROBOT_TOKEN` env reads; no
  secret literal in any committed file (`WEBCTL_TOKEN=` grep → no hits).
- **I5** `tools/deploy_robot.py` refuses online robots without
  `--i-know-robot-is-offline` (implemented phase 1; robot was offline
  all migration).
- **I6** ROLLBACK DRILL, each pre-phase state re-gated (CS-A1–A3):
  ```
  drill→1a45855 (pre 1a):  RESULT PASS / RESULT PASS / SMOKE PASS
  drill→fc8db69 (pre 1b):  RESULT PASS / RESULT PASS / SMOKE PASS
  drill→20a42d9 (pre 4):   RESULT PASS / RESULT PASS / SMOKE PASS
  drill→370fb52 (pre 5):   RESULT PASS / RESULT PASS / SMOKE PASS
  ```
  Method: `git worktree add --detach <pre-phase-commit>` + built dists,
  full A1–A3 in-tree, each run ≈ 3 min (< 10 min budget).
  Note the drill surfaced: pre-phase-4 trees serve the legacy
  `frontend/dist` layout — a rollback to those states needs
  `cd frontend && npm run build` (wheeltec-style dist) first.

## CS-C (deferred, single power-on session)

Not executed — robot offline window is for migration only. No robot-
affecting claims made until CS-C1–C9 run (plan doc tracks it).

## CUTOVER — 2026-11-21 ~21:13 UTC (robot offline window, CS-I5)

- Old hub (wheeltec-webctl tmux `webctl`) stopped; fused packtug hub
  started in tmux `webctl` from `~/packtug-control-panel`
  (`bash scripts/run_server.sh 0.0.0.0 8090`). Same token plane
  (hub token verified sha256-identical to `~/.config/wheeltec-hub-token`).
- Live single-site checks on :8090: `/api/robot` 200 · `/api/health`
  `{"status":"ok","mock":true}` · `/api/arm/status` 200 ·
  `/api/vehicle` shows `[s2000-1 offline, s2000-sim offline]` ·
  `/` 200 · `/agv/` 200 · `/panel/` 200 · asset
  `/assets/index-CvCTk1y8.js` → `text/javascript` (727 kB).
- CS-B2 RE-RUN against the LIVE port: `tools/sim_fleet.py --server
  :8090 --rid s2000-sim` → `SIM FLEET: ALL CHECKS PASSED`.
- `tools/e2e_prod_check.sh` (fixed: was hardcoded `cd
  ~/wheeltec-webctl` + read `.env` instead of `.env.server`) now runs
  repo-relative: v3.7.0 client drove the live hub over real ws
  (`drive ok, clamped=false`), hub returned to `online:false` after.
- s2000-sim added to `WEBCTL_ROBOTS` (random token in `.env.server`,
  harness copy `.scratch-simfleet-token`, both 600 + gitignored).
- Legacy standalone bridge :8020 NOT relaunched — retired by the fused
  design (one port now serves everything).
- Tag `v3.7.6-live-fused`.
