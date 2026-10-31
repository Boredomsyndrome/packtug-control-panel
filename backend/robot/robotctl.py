"""Robot-facing endpoints — the robot is a PURE CLIENT; it calls these.

    GET  /robotctl/<id>/cmd?seq=N&wait=0.4   long-poll for the effective cmd
    POST /robotctl/<id>/telemetry            {battery_v, applied_vx/wz, ...}
    POST /robotctl/<id>/frame                raw JPEG body (camera relay)

Auth: HTTP Basic with username = robot id, password = that robot's token
(WEBCTL_ROBOTS registry). This is separate from the browser Basic Auth, and
BasicAuthMiddleware deliberately skips /robotctl/ so the two credential
systems never mix. All responses are zero-safe: a robot that just applies
whatever `cmd` returns can never receive a stale or out-of-range velocity.
"""
import base64
import json

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from robot.hub import registry


def _err(status, msg, www_authenticate=False):
    resp = JsonResponse({"ok": False, "error": msg}, status=status)
    if www_authenticate:
        resp["WWW-Authenticate"] = 'Basic realm="wheeltec-robotctl"'
    return resp


def _authenticate(request, robot_id):
    """Return (hub, error_response). Unknown id -> 404, bad creds -> 401."""
    hub = registry.robot(robot_id)
    if hub is None:
        return None, _err(404, "unknown robot")
    if not registry.owns:
        return None, _err(503, "this process does not own the robot hub "
                               "(run exactly one server process)")
    username, token = "", ""
    header = request.headers.get("Authorization", "")
    if header.startswith("Basic "):
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
            username, _, token = decoded.partition(":")
        except (ValueError, UnicodeDecodeError):
            pass
    if not registry.check_token(robot_id, username, token):
        return None, _err(401, "bad robot credentials", www_authenticate=True)
    return hub, None


@require_GET
def cmd_poll(request, robot_id):
    """Long-poll. Query: seq (last applied generation, omit on first call),
    wait (seconds to block, 0..1, default 0.4). Always returns the *effective*
    command: zero if latched e-stop or the held command went stale."""
    hub, err = _authenticate(request, robot_id)
    if err:
        return err
    try:
        raw_seq = request.GET.get("seq")
        seq = int(raw_seq) if raw_seq not in (None, "") else None
        wait = float(request.GET.get("wait", 0.4))
    except ValueError:
        return _err(400, "seq must be an integer, wait a number")
    if seq is not None and seq < 0:
        return _err(400, "seq must be >= 0")
    return JsonResponse({"ok": True, **hub.poll(seq, wait)})


@csrf_exempt
@require_POST
def telemetry(request, robot_id):
    """Telemetry uplink from the robot client (JSON, <= 8 KB)."""
    hub, err = _authenticate(request, robot_id)
    if err:
        return err
    body = request.body  # Django caps small non-multipart bodies naturally
    if len(body) > 8192:
        return _err(413, "telemetry too large")
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return _err(400, "telemetry must be valid JSON")
    if not isinstance(data, dict):
        return _err(400, "telemetry must be a JSON object")
    from robot.serializers import TelemetrySerializer
    ser = TelemetrySerializer(data=data)
    if not ser.is_valid():
        return _err(400, f"invalid telemetry: {ser.errors}")
    hub.set_telemetry(ser.validated_data)
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
def frame(request, robot_id):
    """One camera JPEG per request. Keeps WSGI streaming out of the picture:
    bodies are tiny and stateless, so upload framing bugs can't wedge the
    fleet. Robot sends ~10-15 fps; the browser side gets a live relay at /cam/<id>."""
    hub, err = _authenticate(request, robot_id)
    if err:
        return err
    length = request.headers.get("Content-Length")
    if length and int(length) > settings.MAX_FRAME:
        return _err(413, f"frame exceeds MAX_FRAME ({settings.MAX_FRAME} B)")
    jpeg = request.body
    if not jpeg.startswith(b"\xff\xd8"):
        return _err(400, "body is not a JPEG")
    hub.set_frame(jpeg)
    return JsonResponse({"ok": True})
