"""ASGI entrypoint: Django HTTP + the robot/viewer WebSocket routes.

Run (production AND ws-capable dev):
    cd backend && ../.venv/bin/python -m uvicorn webctl.asgi:application \
        --host 0.0.0.0 --port 8000 --no-access-log

Exactly ONE process may own the robot hub: the flock in .server.lock is
acquired during ASGI lifespan startup; a second process still boots but the
API answers 503 (same contract as the WSGI/gunicorn fallback, which runs
fine over HTTP-only — clients then use their long-poll transport).

`manage.py runserver` (WSGI) keeps working as a zero-friction HTTP-only
mode; WebSocket routes need this ASGI app.
"""
import asyncio
import logging
import os
import re

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webctl.settings")

import django  # noqa: E402

django.setup()

from django.core.asgi import get_asgi_application  # noqa: E402

django_asgi_app = get_asgi_application()

from robot.hub import registry  # noqa: E402
from robot.ws import robot_ws, viewer_ws  # noqa: E402

log = logging.getLogger("webctl.asgi")

# ---------------------------------------------------------------------------
# Arm/vision bridge (FastAPI, server/arm_server.py) mounted IN-PROCESS.
# Same process, same port: browser sees ONE site. The bridge owns the
# /api/arm|camera|vision|vehicle|health|docs prefixes; everything else is
# Django. Import-guarded: if fastapi/cv2 are missing the hub still serves
# the full robot plane (bridge-absent mode) — robot code is never down
# because a teammate dependency broke.
# MOCK SAFETY: PACKTUG_MOCK defaults to 1. The real Ned1 is only touched
# with PACKTUG_MOCK=0 AND PACKTUG_ARM_HOST set (explicit operator order).
# ---------------------------------------------------------------------------
_PROJECT_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
import sys  # noqa: E402
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

BRIDGE = None
BRIDGE_PREFIXES = (
    "/api/arm", "/api/camera", "/api/vision", "/api/vehicle",
    "/api/health", "/api/docs", "/api/openapi.json",
)

if __import__("os").environ.get("PACKTUG_DISABLED") != "1":
    try:
        from server.arm_server import ArmController, create_app  # noqa: E402

        _mock = __import__("os").environ.get("PACKTUG_MOCK", "1") != "0"
        _arm_host = __import__("os").environ.get("PACKTUG_ARM_HOST", "")
        _controller = ArmController(mock=_mock)
        if (not _mock and _arm_host
                and __import__("os").environ.get("PACKTUG_ARM_CONNECT") == "1"):
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

log = logging.getLogger("webctl.asgi")

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
        await django_asgi_app(scope, receive, send)
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
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                try:
                    owned = registry.acquire_ownership()
                    loop = asyncio.get_running_loop()
                    for hub in registry.hubs.values():
                        hub.set_event_loop(loop)
                    log.info("webctl ASGI up; hub owner=%s robots=%s",
                             owned, ",".join(registry.hubs))
                    await send({"type": "lifespan.startup.complete"})
                except Exception as exc:  # pragma: no cover
                    await send({"type": "lifespan.startup.failed",
                                "message": str(exc)})
                    return
            elif msg["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    # unknown scope type: ignore per ASGI spec
