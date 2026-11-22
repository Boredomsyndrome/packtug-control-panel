"""ASGI entrypoint — v3.8: the whole hub is FastAPI + raw-ASGI websockets.

Run (production AND dev):
    cd backend && ../.venv/bin/python -m uvicorn webctl.asgi:application \
        --host 0.0.0.0 --port 8090 --no-access-log --ws-ping-interval 30 \
        --ws-ping-timeout 120

`application` routes:
  http      → the arm/vision bridge (FastAPI, server/arm_server.py) owns
              /api/arm|camera|vision|vehicle|health|docs prefixes;
              everything else → the robot-plane FastAPI app.
  websocket → /ws/robotctl/<id> (robot uplink) and /ws/cam/<id>
              (browser viewer) — robot/ws.py handlers, framework-neutral.
  lifespan  → robot hub ownership flock + asyncio loop registration.

Exactly ONE process may own the robot hub: the flock in .server.lock is
acquired at lifespan startup; a second process still boots but the robot
API answers 503 (contract unchanged since v1).

IMPORT GUARDS: if the bridge (fastapi extras/cv2) is missing the robot
plane still serves everything (bridge-absent mode) — robot code is never
down because a teammate dependency broke. The reverse also holds now:
Django/DRF/WhiteNoise are gone from requirements entirely.

MOCK SAFETY: PACKTUG_MOCK defaults to 1. The real Ned1 is only touched
with PACKTUG_MOCK=0 AND PACKTUG_ARM_HOST set (explicit operator order).
"""
import asyncio
import logging
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from robot.api import build_app  # noqa: E402
from robot.ws import robot_ws, viewer_ws  # noqa: E402

log = logging.getLogger("webctl.asgi")

ROBOT_APP = build_app()

# ---------------------------------------------------------------------------
# Arm/vision bridge (FastAPI, server/arm_server.py) mounted IN-PROCESS.
# Same process, same port: browser sees ONE site.
# ---------------------------------------------------------------------------
BRIDGE = None
BRIDGE_PREFIXES = (
    "/api/arm", "/api/camera", "/api/vision", "/api/vehicle",
    "/api/health", "/api/docs", "/api/openapi.json",
)

if os.environ.get("PACKTUG_DISABLED") != "1":
    try:
        from server.arm_server import ArmController, create_app  # noqa: E402

        _mock = os.environ.get("PACKTUG_MOCK", "1") != "0"
        _arm_host = os.environ.get("PACKTUG_ARM_HOST", "")
        _controller = ArmController(mock=_mock)
        if (not _mock and _arm_host
                and os.environ.get("PACKTUG_ARM_CONNECT") == "1"):
            try:
                _controller.connect(_arm_host)
            except Exception as _exc:  # pragma: no cover
                log.error("arm initial connect to %s failed: %s",
                          _arm_host, _exc)
        BRIDGE = create_app(_controller)
        log.info("arm bridge mounted in-process (mock=%s)", _mock)
    except Exception as _exc:  # pragma: no cover - bridge-absent mode
        BRIDGE = None
        log.warning("arm bridge NOT mounted (%s) — robot plane unaffected",
                    _exc)

_ID = r"(?P<robot_id>[A-Za-z0-9_-]{1,32})"
WS_ROUTES = [
    (re.compile(rf"^/ws/robotctl/{_ID}/?$"), robot_ws),
    (re.compile(rf"^/ws/cam/{_ID}/?$"), viewer_ws),
]


async def application(scope, receive, send):
    t = scope["type"]
    if t == "http":
        if BRIDGE is not None:
            path = scope.get("path", "")
            for p in BRIDGE_PREFIXES:
                if path == p or path.startswith(p + "/") or path.startswith(p + "?"):
                    await BRIDGE(scope, receive, send)
                    return
        await ROBOT_APP(scope, receive, send)
        return
    if t == "websocket":
        for pattern, handler in WS_ROUTES:
            m = pattern.match(scope["path"])
            if m:
                await handler(scope, receive, send, **m.groupdict())
                return
        await send({"type": "websocket.close", "code": 4404})
        return
    if t == "lifespan":
        # FastAPI app runs its own lifespan (hub flock + loop registration
        # in robot.api._lifespan); the bridge is a plain mounted sub-app and
        # never received lifespan events before either — unchanged.
        await ROBOT_APP(scope, receive, send)
        return
    # unknown scope type: ignore per ASGI spec
