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


def cam_view(request, robot_id):
    hub = registry.robot(robot_id)
    if hub is None:
        return _err(404, "unknown robot")
    # first frame may take a moment after page open if the robot just started
    frame, seen = hub.next_frame(0, timeout=2.0)
    if frame is None:
        return _err(503, "no camera frames from this robot (yet) — "
                         "is its client running with WEBCTL_CAM_URL set?")

    def gen(first, first_seq):
        f, s = first, first_seq
        while True:
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n"
                   b"Content-Length: " + str(len(f)).encode() + b"\r\n\r\n")
            yield f
            yield b"\r\n"
            nxt, s2 = hub.next_frame(s, timeout=5.0)
            if nxt is None:
                continue          # stale link: re-send last frame at 5 s timeout
            f, s = nxt, s2

    resp = StreamingHttpResponse(gen(frame, seen),
                                 content_type="multipart/x-mixed-replace; boundary=frame")
    resp["Cache-Control"] = "no-store, no-transform"
    resp["X-Accel-Buffering"] = "no"   # disable proxy buffering (nginx/Caddy)
    return resp


def index_view(request):
    dist = Path(settings.PROJECT_ROOT) / "frontend" / "dist" / "index.html"
    if not dist.exists():
        return HttpResponse(
            "<h1>wheeltec-webctl</h1><p>Frontend not built yet. "
            "Run <code>cd frontend && npm ci && npm run build</code>.</p>",
            status=200,
        )
    return TemplateResponse(request, "index.html")
