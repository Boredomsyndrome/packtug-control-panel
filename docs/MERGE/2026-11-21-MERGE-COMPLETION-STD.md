# MERGE COMPLETION STANDARD — CS-01…CS-9x
**"Flawless" defined as: every CS below PASSes with archived evidence. Robot NOT required for CS-A/CS-B/CS-I tiers (robot will be offline during migration). CS-C tier is a deferred, single power-on session.**

> **STATUS 2026-11-21**: CS-A1–A5, B1–B5, I1–I6 GREEN with archived runs in
> `docs/MERGE/EVIDENCE-v3.7.md` (B2 = tools/sim_fleet.py 16/16; I6 drilled ×4).
> CS-A6 :8020 leg SUPERSEDED by in-process fusion (live :8090/api/health check
> belongs to cutover); CS-C deferred — robot offline the whole window.
Baseline captured 2026-11-21 19:11 UTC on `~/wheeltec-webctl` @ `876e044` + `~/packtug-control-panel` @ `ca265cc`. Evidence dir: `~/wheeltec-webctl/.hermes/plans/baseline/` (per-phase reruns go to `baseline/phaseN/`).

---

## Tier A — Offline gates (robot-free; rerun after EVERY merge-phase commit)
| ID | Check | Command (from repo root) | PASS = | Baseline 11-21 |
|---|---|---|---|---|
| CS-A1 | ROS shim suite | `.venv/bin/python -u tools/test_ros_shims.py` | exit 0 + `RESULT: ALL CHECKS PASSED` | ✅ |
| CS-A2 | Hub integration (owns :WEBCTL_LOCK, coexists w/ prod) | `.venv/bin/python -u tools/integration_test.py` | exit 0 + `ALL CHECKS PASSED` (76 checks) | ✅ |
| CS-A3 | Prod launcher smoke (:8901 self-spawn) | `bash tools/prod_smoke_test.sh` | exit 0 + `PROD SMOKE: ALL CHECKS PASSED` (10 checks) | ✅ after harness fix `876e044` — script carried stale pre-v3.2 401 expectations (network-trust policy made it unpassable; fixed + committed) |
| CS-A4 | Panel build (theirs) | `npm run build` (PATH+=~/.local/bin) | exit 0 (tsc+build) | ✅ 11-20 |
| CS-A5 | Bridge tests (theirs) | `PYTHONPATH=. .venv/bin/pytest tests -q` + `node tests/digitalTwin.test.mjs` | 23 passed / 0 fail | ✅ 23+6/0 |
| CS-A6 | Live prod health | `curl :8090/api/robot` → 200; `curl :8020/api/health` → `"mock":true` | both | ✅ (post host-reboot recovery: tmux webctl + bridge relaunched 11-21) |

## Tier B — Simulated-robot acceptance (robot-free; built during Phase 1/4 work)
| ID | Scenario | Method | PASS = |
|---|---|---|---|
| CS-B1 | **Real action-client route path** (the P0 fix) | Extend `test_ros_shims` fake `ClientGoalHandle` to EXACTLY the Humble API: `get_result_async()` → `GetResult.Response(.result=Result(missed_waypoints))`, and DELETE `result_future` from any fake; new integration check drives route through it | route completes via get_result_async callback; a `result_future` reference anywhere in `robotctl/` is a CI-red pattern: `! grep -rn result_future robotctl/*.py` |
| CS-B2 | Sim-fleet loop | `client.py`+`navd.py` run LOCALLY under shims against prod hub with id `s2000-sim`: hub nav buttons → mode transitions localizing→mapping→route→done, estop latch, 409 lockout, map-on/off cycle | all transitions observed via hub API within timeouts; token via test env only |
| CS-B3 | Fused-hub contract (Phase 4) | integration test hits :8090 ONLY: `/api/health` bridge-json, `/api/arm/status` mock, `/api/vehicle` shows `s2000-sim`, ws push latency sample < 150 ms while a 30 s MJPEG-proxy stream is held | all green; import-guard sim: break bridge import → hub still serves (bridge-absent mode) |
| CS-B4 | Panel contract | `npm run typecheck` + Playwright-less: built dist served by hub at `/panel/`, `curl /panel/` 200 + asset hashes; Fleet chip logic unit-tested against mock health (`vehicle_backend` field) | green |
| CS-B5 | Deploy artifact parity | `tools/deploy_robot.py --dry-run` (or render sim): file list + version strings == repo; `bash -n` every .sh; `python -m py_compile` every robot .py; fake-$HOME trap sim for navctl sed renders | all parse/render; no stale file on manifest |

## Tier I — Integrity rules (machine-checked at every phase)
| ID | Rule | Check |
|---|---|---|
| CS-I1 | Vendored bridge = upstream tag, zero hand edits | `diff -r backend/armbridge/ <(git -C ~/packtug-control-panel archive ca265cc server/) ` → empty; tag `v3.6.6-fusion-base` exists |
| CS-I2 | Robot plane single-source | packtug `deploy/nav/*` carry generated-from header after Phase 6; until then: `diff` ours↔theirs only changes in this plan's port table |
| CS-I3 | Version discipline | every phase ends tagged `v3.7.x-<phase>`; `git status` clean; hub `VERSION`/navctl `# v3.7.x` header match |
| CS-I4 | Token hygiene | `grep -rE "WEBCTL_TOKEN=.+" deploy/ robotctl/*.env* ` → only `WEBCTL_TOKEN_FILE` style; live secrets never in any diff |
| CS-I5 | No-merge-during-flight | robot OFFLINE is the migration safety window — `deploy_robot.py` refuses unless `--i-know-robot-is-offline` set (add this guard during Phase 1) |
| CS-I6 | Rollback drill actually drilled | for each phase: revert commit on a scratch branch, rerun CS-A1–A3 green within 10 min |

## Tier C — Robot power-on pack (DEFERRED; one session, in THIS order, evidence = `robot_pack_$(date).tar`)
Run via tmux pane; every step's output tee'd to file. STOP at first hard failure; roll back per FULL-MERGE-PLAN §10.
| ID | Step | Command | PASS = |
|---|---|---|---|
| CS-C1 | Pre-deploy recon (which build is live?) | `head -25 ~/robotctl/navctl.sh`; `pgrep -af webctl_navctl_ekf\|webctl_odom_relay`; `grep WEBCTL_SERVER ~/robotctl/robotctl.env ~/.config/wheeltec-robotctl.env` | recorded; if THEIR v3.6.6-fork is live → note hub they dial, proceed with deploy (ours becomes canonical) |
| CS-C2 | Deploy | `tools/deploy_robot.py --i-know-robot-is-offline` | manifest matches CS-B5 |
| CS-C3 | Client boot | `~/robotctl/run_client.sh ~/robotctl/robotctl.env` (or their boot entry) | hub sees s2000-1 online, 10 Hz telemetry, token from env file |
| CS-C4 | Nav bring-up | `navctl.sh map-on` via hub button | ≤300 s "mapping up"; TF probe `tf2_echo odom_combined base_footprint` @50 Hz & `base_footprint laser` @18 Hz; /scan_filtered hz ≈10; slam drop lines ≈0 steady (skill: bursts ≤5 first 10 s ok) |
| CS-C5 | **P0 route live** | hub waypoint route to nearest recorded wp | mode=route held, 409 lockout on teleop, completes, `missed=0`, NO `goal:` error string in navd log — this verifies CS-B1 on real Humble rclpy |
| CS-C6 | Estop + watchdog | hub estop → zero cmd; client link-drop sim | latched zero, /stop unlatch |
| CS-C7 | CPU recon under load | `bash cpu_recon.sh` during map-on drive | archived; slam% recorded vs profile decision (Phase 2) |
| CS-C8 | Cold boot | full power-cycle (Comet) | client auto-dials hub, watchdog cron present, nav NOT auto-started, single TF owner (`pgrep -af webctl_.*relay` = 0 unless fallback clause invoked) |
| CS-C9 | Sign-off tar | `tar czf robot_pack_*.tar *` evidence | attached to session |

## Completion definition (flawless = all true)
1. CS-A1–A6 green rerun after the LAST merge commit. 2. CS-B1–B5 green. 3. CS-I1–I6 green incl. drilled rollbacks. 4. CS-C1–C9 executed in one session with evidence archived (can lag migration; migration is NOT declared broken while C-pending, but no demo claims until C passes — robot-affecting claims stay honest). 5. FULL-MERGE-PLAN checkboxes updated in-place; plan doc hash-anchored to final tag.

## Standing findings (state as of writing)
- Harness bit-rot is real: CS-A3 was unpassable since v3.2 — fixed in `876e044` (harness only, no merge code). Lesson: every CS keeps a literal expected-string; gates age with policy.
- Host rebooted 2026-11-21 18:56 UTC → tmux webctl + bridge were down; restored same day. **Post-reboot checklist belongs in CS-A6.**
- Robot offline ~2 days = ideal migration window per CS-I5.
- P0 (CS-B1/C5 pair) remains the highest-risk item: the fix is provable offline ONLY with the exact-Humble-API shim — shim fidelity IS the standard here.
