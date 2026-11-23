# EVIDENCE-v3.8 — Django -> FastAPI migration (2026-11-21)

Scope: the robot plane (everything that was Django/DRF/WhiteNoise) is now
the SAME FastAPI app as the arm bridge. `webctl.asgi:application` =
bridge prefixes -> robot FastAPI app; raw-ASGI websockets unchanged.
Route bodies + error shapes byte-compatible: robot client and both SPAs
required ZERO changes. Net -129 lines; requirements lost Django, DRF,
whitenoise, drf-spectacular, gunicorn.

## Robotless gates (all re-run post-migration)
- `tools/integration_test.py` -> **RESULT: ALL CHECKS PASSED** (fleet,
  Basic-auth long-poll, ws uplink + takeover, nav lifecycle, maps/waypoints,
  CS-B3 bridge contract incl. MJPEG-load ws latency 1ms, CS-B4 /panel/ +
  real JS asset).
- `pytest tests -q` 23 passed · `node tests/vehicleChip.test.mjs` 4/4.
- `tools/sim_fleet.py` (self-boot) -> SIM FLEET: ALL CHECKS PASSED (16/16).
- `tools/sim_navctl_render.sh` -> ALL CHECKS PASSED.
- `tools/prod_smoke_test.sh` -> ALL 10 CHECKS PASSED, exit 0.
  Pitfall found+fixed: it shared the default `.server.lock` with the
  LIVE hub -> non-owner 503s; now pins `WEBCTL_LOCK=.server.smoke.lock`.

## Live cutover (:8090, robot offline window, CS-I5)
- tmux `webctl` restarted from the SAME repo/lock/token env — new code live.
- :8090 checks: /api/robot 200 · /api/health mock:true · /api/arm/status 200
  · / /agv/ /panel/ /docs 200 · asset content-type text/javascript.
- sim_fleet against the LIVE port -> ALL CHECKS PASSED.
- e2e_prod_check.sh exit 0 (v3.7.0 client drove live hub over real ws;
  hub back to online:false for all robots after).
- Rollback: `git revert 6bc5b79` restores the fully-gated Django plane
  (tag `v3.7.6-live-fused`).

## Behaviour deltas (deliberate, documented)
- `/api/schema` + `/api/redoc` -> 302 to `/docs` (FastAPI OpenAPI now).
- Status JSON is a superset: adds `odom` (was dropped by the DRF field list).
- `DJANGO_*` env vars are inert (use `PACKTUG_DEBUG`); `.env.server` keys
  unchanged.
