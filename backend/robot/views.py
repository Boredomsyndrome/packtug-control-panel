"""Non-DRF views: the MJPEG camera relay and the SPA index.

The robot client PUSHES each camera JPEG to /robotctl/<id>/frame (it is a
pure client — we never connect to it). The browser gets a live
multipart/x-mixed-replace relay of the newest frame at /cam/<id>. Serving
same-origin also keeps the stream behind the same Basic Auth + HTTPS as the
rest of the app.
"""
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.template.response import TemplateResponse

from robot.hub import registry


def _err(status, msg):
    return JsonResponse({"ok": False, "error": msg}, status=status)


async def cam_view(request, robot_id):
    """ASGI-native MJPEG relay (frames arrive via the robot's websocket).

    A sync generator here breaks under uvicorn: Django ASGI warns that a
    sync-iterator StreamingHttpResponse is consumed per-part through a
    thread-serialised bridge and effectively never streams. Under the WSGI
    fallback Django wraps this async view itself, so one implementation
    serves both."""
    import asyncio

    hub = registry.robot(robot_id)
    if hub is None:
        return _err(404, "unknown robot")
    if hub._loop is None:                    # WSGI path: adopt this loop so
        registry_hubs_loop()                 # hub wakeups reach our events

    ev = asyncio.Event()
    remove = hub.add_frame_listener(ev)
    frame, seen = hub.frame_now()
    if frame is None:
        try:          # first frame may take a moment after the robot starts
            await asyncio.wait_for(ev.wait(), 2.0)
        except TimeoutError:
            remove()
            return _err(503, "no camera frames from this robot (yet) — "
                             "is its client running with a camera enabled?")
        ev.clear()
        frame, seen = hub.frame_now()
        if frame is None:
            remove()
            return _err(503, "no camera frames")

    async def gen():
        try:
            f, s = frame, seen
            while True:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n"
                       b"Content-Length: " + str(len(f)).encode() + b"\r\n\r\n")
                yield f
                yield b"\r\n"
                try:
                    await asyncio.wait_for(ev.wait(), 5.0)
                except TimeoutError:
                    pass                     # stale link: re-send last frame
                ev.clear()
                cur, seq = hub.frame_now()
                if cur is not None and seq > s:
                    f, s = cur, seq
        finally:
            remove()

    resp = StreamingHttpResponse(gen(),
                                 content_type="multipart/x-mixed-replace; boundary=frame")
    resp["Cache-Control"] = "no-store, no-transform"
    resp["X-Accel-Buffering"] = "no"   # disable proxy buffering (nginx/Caddy)
    return resp


def registry_hubs_loop():
    import asyncio
    loop = asyncio.get_running_loop()
    for hub in registry.hubs.values():
        if hub._loop is None:
            hub.set_event_loop(loop)


def index_view(request):
    dist = Path(settings.PROJECT_ROOT) / "frontend" / "dist" / "index.html"
    if not dist.exists():
        return HttpResponse(
            "<h1>wheeltec-webctl</h1><p>Frontend not built yet. "
            "Run <code>cd frontend && npm ci && npm run build</code>.</p>",
            status=200,
        )
    return TemplateResponse(request, "index.html")
