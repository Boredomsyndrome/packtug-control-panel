# Backend Fusion Analysis: Django hub × FastAPI arm bridge

**Status:** ANALYSIS ONLY — explicitly not implemented (Comet: "do not do it yet").
**Date:** 2026-11-20. Code states: `~/wheeltec-webctl` @ b7c8bcf (v3.6.6) · `~/packtug-control-panel` @ **ca265cc** (pulled today; arm_server.py now **4746 ln / 54 endpoints**, gained hand-eye wrist calibration, jog streams, pick-path guards).

## Structural facts grounding the options

- Bridge entrypoint is already fusion-shaped: `create_app(controller) -> FastAPI` is a **pure ASGI app factory**; single `ArmController` singleton holds all state (threads inside it, one TCP session to the arm); all routes registered inside the factory; pydantic v2 request models.
- Bridge traffic shape: **no WebSockets, no asyncio tasks** — sync `def` routes (FastAPI threadpool) + blocking `urllib` to the hub + `StreamingResponse` MJPEG passthrough.
- URL-space overlap between the two backends: **zero**. Hub owns `/api/robot/*`, `/robotctl/*`, `/ws/*`, `/` (whitenoise SPA). Bridge owns `/api/arm/*`, `/api/camera/*`, `/api/vision/*`, `/api/vehicle*`, `/api/health`.
- Same runtime: Python 3.14, uvicorn[standard] 0.54 both sides. Hub venv already has numpy; bridge adds fastapi, pydantic, opencv-python-headless (~50 MB), pyniryo lazily imported only.
- Our hub `backend/webctl/asgi.py` ALREADY does path-prefix dispatch (matches `/ws/` before StaticFilesRouter) — a proven, in-repo pattern to extend.
- Hub hard constraint: exactly one uvicorn process owns the fleet (flock). Fusion keeps one process → constraint untouched.

## Option A — In-process ASGI mount (RECOMMENDED)

One uvicorn on :8090. `asgi.py` grows a prefix router that forwards bridge paths to the FastAPI app **unmodified scope** (no prefix strip — routes are absolute), everything else falls through to Django:

```python
# backend/webctl/asgi.py (sketch)
BRIDGE_PREFIXES = ("api/arm", "api/camera", "api/vision", "api/vehicle", "api/health")
try:
    from armbridge.server import create_app          # import-guarded
    bridge = create_app(mock=os.getenv("PACKTUG_MOCK") == "1")
except Exception:
    logger.exception("arm bridge disabled"); bridge = None

async def application(scope, receive, send):
    if bridge and scope["type"] in ("http",) and scope["path"].lstrip("/").startswith(BRIDGE_PREFIXES):
        return await bridge(scope, receive, send)
    return await django_asgi(scope, receive, send)   # existing ws-first/StaticFiles stack
```

Vendor `packtug-control-panel/server/` into our repo as `backend/armbridge/` (plain package, `create_app` untouched); ArmController singleton constructed once at import. Panel SPA served at **/panel/** (needs `vite base:'/panel/'` + `<BrowserRouter basename="/panel/">`), hub console stays at `/`.

Costs: ~30 lines asgi glue + 2 frontend base tweaks + dep re-pin (`pip freeze` both venvs merged). Their 23 python tests move with the vendored code (PYTHONPATH=backend).
Risks (honest):
1. **Failure coupling — the big one:** 4746 lines of teammate code now shares the process that holds robot safety state (estop latch, cmd mailbox, nav 409 lockout). A hard crash (segfault via OpenCV, OOM on a frame) takes the hub down with it; a runaway threadpool borrower starves ws latency. Mitigations: import-guard; uvicorn threadpool is 40 threads and arm ops are serialized by ArmController's own lock (button-press traffic, not hot path); their MJPEG proxy already caps by streaming from Jetson; commit-level bisectability; **rollback = one revert in asgi.py**.
2. Streaming MJPEG through one process alongside 10 Hz cmd plane — acceptable (threadpool + tiny LAN/WAN frame rates), but must be gated with the integration suite's latency check (push <150 ms).
3. Token-in-log warning applies to merged access log too.
4. pyniryo on Python 3.14 unverified (lazy import keeps `--mock`/hub-boot unaffected; real-arm boot test needed).

## Option B — Rewrite as DRF app inside hub
4746-line port, pydantic→serializers, 23 passing tests rewritten, zero functional gain, 2–3 days risk of behavioral drift in collision-fault/safety logic (their last-safe-point latch semantics are subtle). **Reject.**

## Option C — Keep two processes (current)
Zero fusion risk, but two deps trees, two ports, two restarts, and panel↔hub proxy hop forever. Keep as **fallback** if Option A gate runs show latency or stability regressions — vendored `armbridge/` stays intact either way, so A→C downgrade is just unmounting.

## Option A+ (later, optional): decomposition for hygiene
Split the 470-line `create_app` into `armrouter.py / camera_router.py / vision_router.py` (`fastapi.APIRouter`s, mounted by the factory) after merge settles — maintainability win, still zero DRF rewrite. Only after the team ships the demo (their working tree is active — do NOT fork their internals mid-course).

## Acceptance gates if we ever build A
1. Existing hub gates 1→4 green (shims, 76-check integration, :8901 smoke, e2e-live) with bridge imported.
2. `pytest backend/armbridge/tests` → 23 passed.
3. New integration check: through the fused :8090 only — `/api/health` (bridge json), `/api/arm/status` (mock), `/api/vehicle` (real hub loopback, must show s2000-1), ws push latency still <150 ms.
4. Panel at `/panel/` builds + renders Fleet live (same check done manually today).
5. Robot safety untouched: estop + route-409 regression via `e2e_prod_check.sh`.

## Recommendation
**Option A** when Phase 2 of the merge plan comes due — it's ~a day of work, reverses in one file, and their app factory was accidentally perfect for it. Wait until the teammate's course deadline passes to vendor, or vendor a pinned upstream tag (`v3.6.6-fusion-base` = ca265cc) and re-sync by pull, never hand-edit the vendored copy (their repo stays arm/vision upstream; ours stays robot upstream).

## Open questions for Comet
1. Fuse now vs after their demo deadline? (vendoring churn both ways until they stabilize)
2. Panel base path: `/panel/` OK, or do we flip (panel at `/`, hub console at `/console/`)?
3. Arm bridge with REAL arm lives where? Bridge-in-hub runs on potatoserver2, which cannot see the Ned1 (arm is on the robot's Jetson via UGREEN link) — real-arm mode forces either bridge-on-Jetson (→ Option C!) or Tailscale/VPN route to the arm IP. **This geographic constraint is the strongest argument for hybrid: mock/fused here, real bridge stays on the Jetson.**
