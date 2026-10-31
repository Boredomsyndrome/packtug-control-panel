"""Non-DRF views: the MJPEG camera stream proxy and the SPA index.

The JSON API lives in robot/viewsets.py + robot/serializers.py.
Streaming needs a plain Django StreamingHttpResponse (DRF renderers would
buffer/serialize and break the endless MJPEG multipart stream).
"""
from pathlib import Path

import requests
from django.conf import settings
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.template.response import TemplateResponse

from robot.bridge import bridge


def _err(status, msg):
    return JsonResponse({"ok": False, "error": msg}, status=status)


def cam_view(request):
    """Stream MJPEG from web_video_server through this server (same-origin,
    works behind HTTPS/Caddy without mixed-content issues)."""
    url = f"{settings.CAMERA_BASE_URL}/stream"
    params = {"topic": settings.CAMERA_TOPIC}
    try:
        upstream = requests.get(url, params=params, stream=True, timeout=10)
    except requests.RequestException as e:
        return _err(502, f"camera upstream unreachable: {e}")
    if upstream.status_code != 200:
        body = upstream.content[:300].decode("utf-8", "replace")
        upstream.close()
        return _err(502, f"camera upstream returned {upstream.status_code}: {body}")
    ctype = upstream.headers.get("Content-Type", "multipart/x-mixed-replace; boundary=frame")

    def gen():
        try:
            for chunk in upstream.iter_content(chunk_size=4096):
                yield chunk
        finally:
            upstream.close()

    resp = StreamingHttpResponse(gen(), content_type=ctype)
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
