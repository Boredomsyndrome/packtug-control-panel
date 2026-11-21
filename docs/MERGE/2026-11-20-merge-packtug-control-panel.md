# PackTug Control Panel × wheeltec-webctl Merge Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Run one co-located system: our battle-tested Django hub owns the robot/nav plane, the PackTug FastAPI bridge owns the arm/vision plane, and the PackTug React panel becomes the operator UI — with the robot-client fork re-converged into our repo as the single source of truth.

**Architecture:** Two uvicorn processes on potatoserver2 (Django hub :8090, arm bridge :8020, localhost-linked). Robot-side `~/robotctl` deployed only from our repo. No merge of the two backends into one process (YAGNI — the bridge is hardware-local glue; the hub is the safety-critical fleet plane).

**Tech Stack:** Python 3.14 + Django 6.1.2/DRF 3.18.3 (hub) · Python 3.14 + FastAPI 0.165/pydantic 2.14/opencv-headless (bridge) · both served by uvicorn[standard] 0.54.0 · Vite 5 + React 18 + TS 5 + react-router 6 + recharts (panel SPA) vs Vite 5 + React 18 JSX no-TS (webctl console) · robot: stdlib client + rclpy (ROS 2 Humble).

## Stack comparison (verified 2026-11-20)

| Layer | wheeltec-webctl (ours) | packtug-control-panel (theirs) |
| --- | --- | --- |
| Language | Python 3.14 venv | Python 3.14 venv (`.venv`, built here) |
| Web | Django 6.1.2 ASGI + DRF 3.18.3, raw-ASGI ws (no channels), whitenoise 6.12 | FastAPI 0.165 + pydantic 2.14, raw uvicorn, StaticFiles(html) |
| Server | uvicorn[standard] 0.54.0 (gunicorn 26 spare), flock single-owner, :8090 | uvicorn 0.54.0, single process, :8020, `--mock` mode |
| Extra deps | python-dotenv, drf-spectacular | numpy 2.5.5, opencv-python-headless 5.1, pyniryo 1.1.2 (lazy import) |
| Robot code | `robotctl/` (client.py 1393 ln, navd.py, scan/tf helpers) + `deploy/navctl.sh` v3.6.6 | `deploy/nav/` fork at v3.6.5 base + own v3.6.6-map-save-timeout |
| Frontend | `frontend/` React 18 JSX, hand-rolled router-less SPA, LiDAR canvas, nav UI | `src/` React 18 TS, 13 pages, recharts, isometric 3D packer |
| Tests | shim suite (~50) + integration (76 checks) + prod smoke + e2e-live | `tests/test_pick_targets.py` (unittest) + `tests/digitalTwin.test.mjs` (node:test) |
| Security | per-robot `?token=`, network-trusted browsers, LAN/VPN only | none (relies on network + our hub trust) |

## Merge decision matrix (robot-side fork)

Port INTO our repo:
- `navd.py` → direct rclpy map-save (`_save_map_python`) + map/AMCL snapshot locks (`_map_lock/_tf_lock/_node_lock`, `_map_sub`, `_amcl_msg`) — fixes real map_saver_cli transient-local timeout.
- `navigate_no_spin_backup.xml` + navctl BT-xml wiring (towed cage must never spin/reverse backup).
- Box-mask widening `x_back: 0.50 → 1.10` — **parameterize** via env, keep 0.50 default.
- `watchdog_vpn.sh` quoted/unquoted `WEBCTL_SERVER` sed parse (superset of ours).
- `client.py` `/odom` telemetry passthrough ONLY if it adds telemetry keys we lack — the odom→TF broadcast block itself is NOT ported (see rejects).

REJECT (keep our v3.6.6 behavior) — each with recorded evidence:
- `FASTRTPS_DEFAULT_PROFILES_FILE` re-add (our `b7c8bcf`: DDS island; their relay/mirror saga likely stems from it).
- EKF removal + `webctl_odom_relay.py` + client-embedded odom TF + scan_filter TF mirror. Ours: robot_localization EKF @50 Hz verified TF tree 50/50/18 Hz post-reboot. Their trio is triple-broadcast of one transform. Fallback clause: ONLY if the merged stack reproduces "AMCL earlier-than-all-data while parked" on the real robot do we adopt the standalone relay — and then as the SOLE broadcaster.
- Their tf2_echo `ros2 run` spawn (ours: direct binary; 80 s cold-start vs 120 s recycle window).

## Phase 0 — Decisions & freeze (needs Comet)

1. **One hub:** potatoserver2 `:8090` is canonical; teammate's Mac hub (100.86.19.104) becomes dev-only. Robot env `WEBCTL_SERVER` stays our LAN+VPN list; robot id stays `s2000-1` (theirs was `wheeltec`).
2. Confirm same physical robot (their AGENTS: S200+Jetson+UGREEN arm link = our S2000 unit).
3. Freeze both robot-client trees with tags.

Tasks:
- [ ] In `~/wheeltec-webctl`: `git tag v3.6.6-premerge-ours HEAD`; in `~/packtug-control-panel`: `git tag v3.6.6-mapsave-fork HEAD`. Commit both repos' states first (`git status` clean-ish or WIP-commit).
- [ ] Record the decision list above in `docs/API.md` appendix (patch tool, not heredoc — guard trap).

Verify: `git -C ~/wheeltec-webctl tag | grep premerge` shows tag; `git -C ~/packtug-control-panel tag | grep mapsave` shows tag.

## Phase 1 — Robot-client convergence (our repo = truth)

All edits in `~/wheeltec-webctl`. Robot deploys ONLY via `tools/deploy_robot.py`/`deploy_to_robot.sh` after gates; motion stays off (`route` only on Comet's explicit live order).

- [ ] **1.0 P0 BUG-FIX FIRST — Humble action API (found by their fork, VERIFIED against ros2/rclpy@humble source: `ClientGoalHandle` has ONLY `get_result_async()`, no `result_future` attr).** Our `robotctl/navd.py:299` does `goal.result_future.add_done_callback(...)` → guaranteed AttributeError inside `_goal_cb`'s try → every REAL route flips error `goal: 'ClientGoalHandle' object has no attribute ...` + mode=localizing instantly; `_result_cb` also reads `res.missed_waypoints` but Humble delivers a `GetResult.Response` wrapper (they unwrap: `payload = getattr(res, "result", res)`). Dry-FSM integration tests never reach the real action client → bug hidden on robot. Port BOTH hunks from `~/packtug-control-panel/deploy/nav/navd.py:310-335`; THEN harden the shim: give the fake rclpy a ClientGoalHandle with `get_result_async()` returning a future resolving to a Response-wrapping-Result (mirror real shape), add integration check "route uses get_result_async path". Verify: shim gate + on-robot route smoke (Comet's live order; robot currently OFFLINE ~2 days).
- [ ] **1.1** Port navd map-save: copy `_save_map_python()` from `~/packtug-control-panel/deploy/nav/navd.py` (grep `def _save_map_python`) into our `robotctl/navd.py`; in the map-save call site replace `if self._navctl(["map-save", ...])` with python-first/CLI-fallback as in their hunk. Verify: `.venv/bin/python -u tools/test_ros_shims.py` → RESULT line, filter `grep -E "FAIL|RESULT"`.
- [ ] **1.2** Port navd lock/snapshot block (`_map_lock`, `_map_sub`, `_amcl_msg`, `_clear_amcl` call sites). Verify: same shim gate.
- [ ] **1.3** Behavior tree: copy `deploy/nav/navigate_no_spin_backup.xml` → our `deploy/nav_params/`; port their navctl sed line that injects the BT xml path into the Nav2 param copy (locate: `grep -n navigate_no_spin ~/packtug-control-panel/deploy/nav/navctl.sh`). Verify: `bash -n deploy/navctl.sh`; render sed on simulated robot env (skill pattern: fake $HOME trap sim).
- [ ] **1.4** Mask parameterization: in `robotctl/webctl_scan_filter.py` turn the box `[−0.50, 0.30, −0.35, 0.35]` into `x_back = float(os.getenv("WEBCTL_MASK_X_BACK", "0.50"))`; cage-towing profile = `WEBCTL_MASK_X_BACK=1.10` in robotctl.env. Verify: shim gate scan-pack checks.
- [ ] **1.5** `deploy/watchdog_vpn.sh`: adopt their `sed -n 's/^WEBCTL_SERVER=//p' … | tr -d '"'` parse. Verify: `bash -n` + sourced-line unit echo on a quoted and an unquoted fixture env in scratch.
- [ ] **1.6** Full gates IN ORDER (`.venv/bin/python -u`, tmux kill-session webctl first, relaunch + e2e after): `tools/test_ros_shims.py` → `tools/integration_test.py` (ALL CHECKS PASSED) → `prod_smoke_test.sh` → deploy to robot (Comet clicks consent card; re-ask expected) → `e2e_prod_check.sh`.
- [ ] **1.7** Robot live verification (COMET MUST ORDER MAPPING IN-SESSION): `navctl.sh version` reports merged build; map-on → TF tree 50/50/18 Hz, zero `queue is full` drops steady-state, hub pose map-frame. If parked-AMCL drops appear → fallback clause (standalone relay, sole broadcaster), else delete `webctl_odom_relay.py` from consideration.
- [ ] **1.8** Tag `v3.7.0-cage` + regenerate tarball (absolute path).

## Phase 2 — Bridge co-location on potatoserver2

- [ ] **2.1** Run script (guard-friendly): create `~/packtug-control-panel/run_bridge.sh` wrapping `.venv/bin/python server/arm_server.py --mock --host 127.0.0.1 --port 8020`; bridge stays loopback-only (panel is for Comet's browser via LAN → later task or use SSH tunnel).
- [ ] **2.2** tmux session `packtug` (matches webctl convention; survives my session, dies on host reboot — document).
- [ ] **2.3** Their repo tests green on this box: `cd ~/packtug-control-panel && .venv/bin/python -m unittest discover -s tests -t .` (expect OK; import path `server.arm_server` needs repo root as CWD) and `node tests/digitalTwin.test.mjs` (expect `pass 857…` style OK).
- [ ] **2.4** Bridge health wiring check: `curl 127.0.0.1:8020/api/health`, `/api/vehicle` shows s2000-1 from live hub (verified working 2026-11-20).

## Phase 3 — Operator UI unification

Recommendation: panel SPA becomes THE operator surface; webctl console stays for emergency/drive (v3.4 UI spec applies to the webctl SPA: LiDAR-only, no camera pane — the panel's Arm-page camera is arm vision, not AGV sensor, so it's exempt).

- [ ] **3.1** Panel Fleet page: replace dead placeholder text with live hub nav snapshot (GET /api/robot/<id>/nav via bridge proxy passthrough) + LiDAR canvas port from our `frontend/src/App.jsx` sweep code (bridge proxy `/api/vehicle/<id>/scan` → hub `/api/robot/<id>/scan`).
- [ ] **3.2** Panel "AGV Console" page: iframe of hub root (network-trusted, no auth) — one-click in-SPA instead of popup.
- [ ] **3.3** Mock-badge fix: bridge `/api/health` mock flag currently conflates arm-mock with AGV-data; expose `vehicle_backend: {ok,url,robots}` and make the panel chip read that so Fleet says LIVE when hub answers.
- [ ] **3.4** Wire the map-import placeholder: hub already serves `/api/robot/<id>/maps/<name>.{pgm,yaml}` — fetch list, draw occupancy grid as the warehouse map base layer (big win for their scenario pages).
- [ ] **3.5** `npm run typecheck && npm run build` green; serve dist via bridge (already wired); screenshots for sign-off.

## Phase 4 — Decommission / hygiene

- [ ] **4.1** Teammate: robot env on both files (`~/robotctl/robotctl.env` + `~/.config/wheeltec-robotctl.env`) → hub list, no Mac URL; `watchdog_vpn.sh` cron re-armed (it parses WEBCTL_SERVER — stale Mac URL bounces the client against a dead hub).
- [ ] **4.2** Token hygiene: `~/.config/wheeltec-hub-token` remains backup; rotate if Mac-hub logs saw it (their AGENTS warns ws query strings hit ASGI access logs).
- [ ] **4.3** `deploy/nav/` in packtug repo → generated copies (README points at our repo as source) OR git-submodule our repo — pick with teammate; never hand-edit both.
- [ ] **4.4** Docs: our `docs/API.md` gains bridge proxy endpoints; packtug `AGENTS.md` gains hub-canonical note.

## Phase 5 — Final verification

1. All four webctl gates green (1,2,3) + `e2e_prod_check.sh` against live :8090.
2. Panel: Fleet shows live s2000-1 + LiDAR sweep; nav buttons 409-while-routing lockout behaves (teleop guard).
3. Bridge tests + packtug build green.
4. Robot cold-boot test (Comet power-cycles): client auto-dials hub, watchdog_vpn healthy against hub URL, no EKF/relay TF conflict in `ros2 topic` + slam logs.

## Risks / open questions
- **Same robot, two histories:** teammate may have deployed their navctl build to the S2000 recently (their sync commit Nov 19). Before Phase 1.7, recon live robot: `cat ~/robotctl/navctl.sh | head -25` + `pgrep -af webctl_navctl_ekf` to learn who last deployed.
- **Their EKF verdict may bite back:** their docstring claims "EKF cannot discover /odom across DDS sessions on this Jetson" — our EKF remaps `/odometry/filtered:=odom_combined` and consumes `/wheeltec/odom`-style input; if their claim reproduces on OUR stack (unlikely: 50 Hz TF verified post-boot at b7c8bcf), the fallback relay clause applies. Decide with logs, not docs.
- **Tailscale/camera:** D455 stream lives on the Jetson (100.73.98.69:8080) — bridge on potatoserver2 can't reach it until the Jetson joins our VPN (hwdsl2 client) or we Tailscale the server. Phase 2.5 (deferred): pick transport.
- **Arm on robot power budget:** Ned1 + D455 + Nav2 on one Jetson/battery — v3.6.2 lesson: heavy launches drop ws. Arm ops during `route` = forbidden; document + later enforce hub-side 409.
- **Loopback bridge:** panel needs LAN reach for teammates — decide LAN bind (LAN-trust policy same as hub) vs per-user SSH tunnel.
- **Do NOT merge** the two backends into one process: 45-endpoint single-file FastAPI monolith → Django port buys nothing but risk; revisit only if arm_server.py grows stateful fleet logic.
