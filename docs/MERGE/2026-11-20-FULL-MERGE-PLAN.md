# FULL CODE MERGE PLAN — wheeltec-webctl × packtug-control-panel

**Date:** 2026-11-20 · **Status: PLANNING ONLY — repo contains ZERO merge code (owner ordered a full revert; this doc is the single source of truth).**
**Repos & anchors:**
- `~/wheeltec-webctl` @ **b7c8bcf** (v3.6.6, Django hub :8090 + robotctl client) — CLEAN
- `~/packtug-control-panel` @ **ca265cc** (Vite/React panel + FastAPI arm bridge, 4746 ln / 54 endpoints)
- Robot: WHEELTEC S2000 (Jetson Orin Nano Dev, ROS 2 Humble), id `s2000-1`, currently OFFLINE ~2 days
- Superseded drafts (context only): `2026-11-20-merge-packtug-control-panel.md`, `2026-11-20-backend-fusion-analysis.md`, `2026-11-20-jetson-cpu-budget.md`, artifacts in `attachments/`
- **Completion standard (defines flawless; robot-free tiers A/B/I + deferred robot pack C): `2026-11-21-MERGE-COMPLETION-STD.md`** — baselines in `baseline/`; all gates rerun per phase; harness fix `876e044` (smoke v3.2 policy alignment)

---

## 0. Final architecture (target state)

```
[Browser panel /panel/] ──▶ ONE fused hub :8090 ── Django (robot/nav/fleet plane)
                              │  └─ asgi prefix-router → FastAPI bridge (arm/vision plane, mounted in-proc)
                              │        (mock mode on potatoserver2; REAL arm mode ⇒ bridge on Jetson)
        ws /ws/robotctl ──────┤
[robot s2000-1] client.py+navd.py+navctl.sh ← deployed ONLY from ~/wheeltec-webctl
   └ Jetson also runs: packtug-camera.service (:8080) + arm-link net config (packtug repo scripts)
```
Rules of truth: **robot client/nav/Hub ⇐ our repo. Arm/vision/panel ⇐ packtug repo.** Nobody hand-edits the other's tree; `deploy/nav/` in packtug becomes generated copies.

## 1. Decision list (needs Comet ✅ before Phase 1)
| # | Question | Recommendation |
|---|---|---|
| D1 | Canonical hub | potatoserver2 :8090; Mac hub (100.86.19.104) → dev-only |
| D2 | Robot id | keep `s2000-1` |
| D3 | Panel position | panel at `/panel/`, hub console stays `/` |
| D4 | Fuse timing | after teammate's course deadline; vendor their `server/` at pinned tag |
| D5 | Real-arm topology | hybrid: fused mock/dev + real bridge instance on Jetson |
| D6 | CPU work | run recon first, then decide low-CPU profile go |

## 2. Phase 0 — Freeze
- [ ] Tag ours `git tag v3.6.6-premerge-ours`; packtug `git tag v3.6.6-fusion-base ca265cc`.
- [ ] Recon live robot FIRST (their fork may be what's flashed): `head -25 ~/robotctl/navctl.sh`, `pgrep -af webctl_navctl_ekf`, `cat ~/robotctl/robotctl.env | grep WEBCTL_SERVER` (which hub does the client dial?).
- Verify: tags exist both repos; recon output saved to this plan's folder.

## 3. Phase 1 — Robot client merge (our repo = destination)
All in `~/wheeltec-webctl`. Robot deploys ONLY via `tools/deploy_robot.py` after ALL gates. Motion never without Comet's live order.

### 1.0 P0 — real-route bug on Humble (theirs proved ours broken)
Verified vs `ros2/rclpy@humble` source: `ClientGoalHandle` has **only `get_result_async()`**; our `robotctl/navd.py:299` uses `goal.result_future` → AttributeError → every REAL route instantly fails; `_result_cb` also misses the `GetResult.Response` wrapper (`res.result`). Dry-FSM tests never reach this path → hidden.
- [ ] Port both hunks from `packtug .../deploy/nav/navd.py` route start/result callbacks.
- [ ] Harden shim: fake `ClientGoalHandle.get_result_async()` → future resolving to Response-wrapping-Result; new integration check "route completes via get_result_async".
- Verify: shim gate + integration; then on-robot route smoke (ordered by Comet).

### 1.1 Port INTO ours (file → file, with locator)
| What | From | To | Verify |
|---|---|---|---|
| Direct-rclpy map save `_save_map_python` + `_map_sub`/`_amcl_msg`/lock block | `deploy/nav/navd.py` (`grep _save_map_python\|_map_lock`) | `robotctl/navd.py` | shim gate |
| Humble waypoint API (1.0 above) | same file | same | shim+robot |
| No-spin recovery BT `navigate_no_spin_backup.xml` + Nav2 `default_nav_to_pose_bt_xml` wiring in navctl's param sed | `deploy/nav/` + `grep navigate_no_spin deploy/nav/navctl.sh` | `deploy/nav_params/` + `deploy/navctl.sh` | `bash -n` + fake-$HOME sed render sim |
| Box-mask widening param `WEBCTL_MASK_X_BACK` (default 0.50, cage 1.10) | hard-coded 1.10 in their scan_filter | `robotctl/webctl_scan_filter.py` env-ize + robotctl.env profile | shim scan-pack check |
| watchdog_vpn.sh quoted+unquoted `WEBCTL_SERVER` parse | `deploy/nav/watchdog_vpn.sh` | `deploy/watchdog_vpn.sh` | `bash -n` + fixture env echo |
| `/odom` telemetry passthrough (values ONLY, NOT TF broadcast) | their client.py diff (`diff -u` both clients) | `robotctl/client.py` if keys missing | shim + e2e |
| packtug-camera.service + watchdog timer + `setup_camera_service.sh` | `deploy/` | our `deploy/` (Jetson side; camera plane) | `systemd-analyze verify` offline lint |
| `setup_arm_link.sh` (link-local 169.254.200.1/16, forced 100M/FD UGREEN link) | `deploy/` | our `deploy/` + docs | dry-run nmcli echo |

### 1.2 REJECT (keep our behavior; rationale frozen here)
- `FASTRTPS_DEFAULT_PROFILES_FILE` re-add — our b7c8bcf boot forensics = DDS island. Their whole relay/mirror saga plausibly stems from it.
- EKF removal + `webctl_odom_relay.py` + client odom→TF broadcast + scan_filter TF mirror — triple broadcast; our EKF verified 50/50/18 Hz post-boot. **Fallback clause:** ONLY if parked-AMCL "earlier than all data" drops reproduce on OUR merged stack → adopt standalone relay as SOLE broadcaster.
- `ros2 run tf2_echo` spawn — keep direct binary (80 s vs 120 s recycle trap).
- Their `webctl_tf_repeater.py` — our v3.6.5 lesson says params-file/receipt fixes supersede it.

### 1.3 Gates (strict order, `.venv/bin/python -u`, tmux webctl down first, relaunch after)
1 shim suite → 2 integration (`ALL CHECKS PASSED`) → 3 `prod_smoke_test.sh` → 4 deploy (Comet consent) → 5 `e2e_prod_check.sh` → 6 on-robot: version line, map-on, TF tree, 0 steady drops, pose map-frame → tag `v3.7.0-cage` + tarball (absolute path).

## 4. Phase 2 — Jetson CPU budget (before nav-heavy demos)
Full analysis: `2026-11-20-jetson-cpu-budget.md` (keep). Key: nav stack is ALREADY C++ (slam_toolbox/Ceres, Nav2, EKF, rplidar) — no rewrite justified; hog = our responsive mapper params (+ possibly Orin NVP preset).
- [ ] On power-up run `attachments/cpu_recon.sh` (READ-ONLY) during a map-on pass → decision tree: nvpmodel preset → low-CPU profile → scan decimation → `taskset` affinity (slam on cores 2-5; protect client/EKF on 0-1) → last-resort FASTRTPS-SHM.
- [ ] Profile `mapper_rplidar_a1_lowcpu.yaml` (in attachments) → after recon, if slam >90%: wire via ONE line `MAPPER=` in navctl.sh, re-recon, compare map quality.
- Never touch raw `mapper_rplidar_a1.yaml` in-place (keep as A/B baseline).

## 5. Phase 3 — Arm bridge co-location on potatoserver2 (pre-fusion stage)
- [ ] `run_bridge.sh` (loopback bind, `--mock` until real-arm topology decided) + tmux session `packtug`.
- [ ] Their tests green here: `PYTHONPATH=. .venv/bin/pytest tests -q` (23 pass) + `node tests/digitalTwin.test.mjs`.
- [ ] `/api/health`, `/api/vehicle` (live s2000-1 via hub) verified — re-verify after each pull.
- [ ] Re-sync policy: vendor = `git pull` their tag; NEVER hand-edit vendored code.

## 6. Phase 4 — Backend fusion (Option A — in-process ASGI mount; zero rewrite)
Their `create_app(controller)` is a pure ASGI factory; URL spaces disjoint (`/api/robot|ws|robotctl` vs `/api/arm|camera|vision|vehicle|health`); same py3.14+uvicorn 0.54.
- [ ] Vendor `server/` → `backend/armbridge/` (plain package); add fastapi/pydantic/opencv-python-headless to hub `requirements.txt`; `PACKTUG_MOCK=1` env.
- [ ] `backend/webctl/asgi.py`: prefix router BEFORE StaticFilesRouter (same pattern as ws-match) — bridge paths → FastAPI scope verbatim; import-guard ⇒ bridge optional.
- [ ] Panel build: `vite base:'/panel/'` + `<BrowserRouter basename>`; hub whitenoise serves `dist/panel/`.
- [ ] New integration checks through :8090 ONLY: bridge health, arm status mock, `/api/vehicle` loopback, ws latency still <150 ms under MJPEG proxy load.
- [ ] Gates 1→5 all green with bridge imported = fused release; rollback = one asgi.py revert.
- [ ] Later hygiene: split 470-ln `create_app` into 3 APIRouters (their repo first, then vendor).

## 7. Phase 5 — Operator UI unification (panel = THE surface; webctl console = emergency/drive)
- [ ] Fleet: live nav snapshot via bridge passthrough + LiDAR canvas port (from our `frontend/src/App.jsx` sweep) through `/api/vehicle/<id>/scan`.
- [ ] "AGV Console" page = iframe of hub root (network-trusted).
- [ ] Mock-badge fix: bridge `/api/health` expose `vehicle_backend` state; chip reads it (Fleet = LIVE when hub answers).
- [ ] Map import placeholder ← hub `/api/robot/<id>/maps/*.pgm` fetched + drawn as warehouse base layer.
- [ ] `npm run typecheck && npm run build` green + screenshots sign-off.
- UI spec reminder: v3.4 rules (LiDAR-only, no AGV camera pane) apply to webctl console; panel's arm-camera exempt.

## 8. Phase 6 — Decommission & hygiene
- [ ] Robot both env files → hub list URLs only (no Mac); re-arm `watchdog_vpn.sh` cron.
- [ ] Token: rotate if Mac hub logs saw it; `~/.config/wheeltec-hub-token` stays backup.
- [ ] packtug `deploy/nav/` → generated copies with "source = wheeltec-webctl" header.
- [ ] Docs: hub `docs/API.md` + bridge endpoints; their `AGENTS.md` + hub-canonical note.

## 9. Global verification matrix
| # | Check | Pass = |
|---|---|---|
| V1 | hub gates 1-3 (shim/integration/smoke) with bridge mounted | ALL CHECKS PASSED ×3 |
| V2 | bridge suites (pytest 23, node digitalTwin) | all pass |
| V3 | panel build + /panel/ renders Fleet LIVE | screenshot + chip LIVE |
| V4 | on-robot: fused :8090 nav buttons, route 409 lockout, estop latch | e2e_prod_check |
| V5 | on-robot route actually drives (1.0 fix) | waypoint completion, 0 missed |
| V6 | cold boot (Comet power-cycle) | client auto-dials hub, watchdog healthy, no TF conflict |
| V7 | CPU recon re-run post-everything | slam residual documented |

## 10. Rollback matrix
| Phase | Blast radius | Undo |
|---|---|---|
| 1 | robot nav | redeploy `v3.6.6-premerge-ours` tarball |
| 2 | CPU profile | MAPPER= one-liner back |
| 3/4 | bridge/fusion | revert asgi.py commit; hub unaffected if import-guard tripped |
| 5 | panel | serve old dist |
| 6 | env | restore env file from `.bak` (never edit in place without backup) |

## 11. Risk register (top 5)
1. **Their build may be live on the robot** — Phase 0 recon first, always.
2. **Failure coupling after fusion** — their code crashes ⇒ hub crash; mitigations: import-guard, threadpool headroom (40 threads, ArmController serialized), gate V1 under MJPEG load; fallback = Option C (two processes) with vendored package preserved.
3. **P0 route fix untested on robot until powered** — V5 mandatory before any demo uses routes.
4. **Arm+Nav2 on one Jetson/battery** (v3.6.2 lesson: heavy launches drop ws) — later enforce hub-side 409 "arm ops forbidden while route active".
5. **Real-arm geography** (Ned1 only reachable from Jetson UGREEN link) — forces hybrid; decide before promising arm demos from potatoserver2.

## 12. Open questions (blocking as marked)
- [ ] D1–D6 (Table §1) — **D1, D2, D6 blocking Phase 0/2**
- [ ] Teammate: consent to `deploy/nav/` becoming generated copies (Phase 6)
- [ ] Tailscale/VPN membership for Jetson (camera + real arm reachability) — Phase 3/5 camera features
- [ ] Loopback vs LAN bind for pre-fusion bridge (teammate access)

## 13. Execution notes (who/when)
- Suggested order: P0 → P1 → P2 → (teammate deadline) → P3 → P4 → P5 → P6, V-gates interleaved.
- Every phase = commit + tag on green; every robot change = gates then consent card.
- This plan lives at `~/wheeltec-webctl/.hermes/plans/2026-11-20-FULL-MERGE-PLAN.md`; update it, don't fork it.
