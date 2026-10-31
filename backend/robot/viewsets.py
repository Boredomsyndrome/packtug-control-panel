"""DRF ViewSet exposing the robot as one API resource:

    GET  /api/robot          -> status/telemetry (list action on the singleton)
    POST /api/robot/cmd      -> set velocity (validated + clamped)
    POST /api/robot/stop     -> immediate stop, clears latched e-stop
    POST /api/robot/estop    -> emergency stop, latched until next cmd

Authentication is handled by robot.middleware.BasicAuthMiddleware for the
whole site, so DRF itself is AllowAny (see settings.REST_FRAMEWORK).
The MJPEG camera proxy (/cam) intentionally stays a plain Django streaming
view — DRF renderers don't fit endless streams.
"""
from rest_framework import status as http_status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from robot.bridge import bridge
from robot.serializers import (
    CommandResultSerializer,
    RobotStatusSerializer,
    VelocityCommandSerializer,
)

NOT_OWNER = ("this worker does not own the robot bridge "
             "(run exactly one server worker/process)")


class RobotViewSet(ViewSet):
    """Singleton robot resource."""

    def get_permissions(self):
        return []  # middleware handles auth; keep DRF open

    def list(self, request):
        """GET /api/robot — full status + telemetry snapshot."""
        payload = bridge.status()
        serializer = RobotStatusSerializer(payload)
        data = dict(serializer.data)
        data["ok"] = True
        data["camera_path"] = "/cam"
        return Response(data)

    @action(detail=False, methods=["post"])
    def cmd(self, request):
        """POST /api/robot/cmd {"vx":…, "wz":…}."""
        if not bridge.owns_bridge:
            return Response({"ok": False, "error": NOT_OWNER},
                            status=http_status.HTTP_503_SERVICE_UNAVAILABLE)
        serializer = VelocityCommandSerializer(data=request.data if isinstance(request.data, dict) else {})
        if not serializer.is_valid():
            return Response({"ok": False, "error": "invalid command",
                             "errors": serializer.errors},
                            status=http_status.HTTP_400_BAD_REQUEST)
        vx = serializer.validated_data["vx"]
        wz = serializer.validated_data["wz"]
        clamped = bridge.set_cmd(vx, wz)
        result = CommandResultSerializer({
            "ok": True,
            "vx": clamped[0],
            "wz": clamped[1],
            "clamped": clamped != (vx, wz),
        })
        return Response(result.data)

    @action(detail=False, methods=["post"])
    def stop(self, request):
        """POST /api/robot/stop — zero velocity AND clear latched e-stop."""
        bridge.stop()
        return Response({"ok": True})

    @action(detail=False, methods=["post"])
    def estop(self, request):
        """POST /api/robot/estop — latch zero until an explicit new command."""
        bridge.emergency_stop()
        return Response({"ok": True, "estop": True})
