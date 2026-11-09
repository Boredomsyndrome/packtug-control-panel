"""DRF ViewSet — the BROWSER-facing fleet API (pull-mode v2):

    GET  /api/robot            -> every robot's status (fleet dashboard)
    GET  /api/robot/<id>       -> one robot's status
    POST /api/robot/<id>/cmd   -> set velocity (validated + clamped)
    POST /api/robot/<id>/stop  -> immediate stop, clears latched e-stop
    POST /api/robot/<id>/estop -> emergency stop, latched until next cmd

The server never contacts a robot: commands land in the per-robot hub and
the robot picks them up on its next long-poll. Browser auth is site-wide
BasicAuthMiddleware, so DRF itself is AllowAny (settings.REST_FRAMEWORK).
The MJPEG relay (/cam/<id>) stays a plain Django streaming view — DRF
renderers don't fit endless streams.
"""
import time

from rest_framework import status as http_status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from robot.hub import SCAN_FRESH, registry
from robot import navstore
from robot.serializers import (
    CommandResultSerializer,
    NavActionSerializer,
    RobotStatusSerializer,
    VelocityCommandSerializer,
)

NOT_OWNER = ("this process does not own the robot hub state "
             "(run exactly one server process)")


class RobotViewSet(ViewSet):
    """Fleet resource."""

    def get_permissions(self):
        return []  # middleware handles auth; keep DRF open

    def _hub_or_404(self, pk):
        hub = registry.robot(pk)
        return hub, (None if hub else
                     Response({"ok": False, "error": f"unknown robot '{pk}'"},
                              status=http_status.HTTP_404_NOT_FOUND))

    def list(self, request):
        """GET /api/robot — status of every registered robot."""
        robots = [RobotStatusSerializer(s).data for s in registry.summary()]
        return Response({"ok": True, "robots": robots,
                         "camera_path": "/cam/<id>"})

    def retrieve(self, request, pk=None):
        hub, err = self._hub_or_404(pk)
        if err:
            return err
        data = RobotStatusSerializer(hub.status()).data
        data["ok"] = True
        data["camera_path"] = f"/cam/{hub.robot_id}"
        return Response(data)

    @action(detail=True, methods=["post"])
    def cmd(self, request, pk=None):
        """POST /api/robot/<id>/cmd {"vx":…, "wz":…}."""
        hub, err = self._hub_or_404(pk)
        if err:
            return err
        if not registry.owns:
            return Response({"ok": False, "error": NOT_OWNER},
                            status=http_status.HTTP_503_SERVICE_UNAVAILABLE)
        if hub is not None and hub.nav_mode == "route":
            # UX layer only — the ROBOT also gates its teleop publisher while
            # Nav2 drives; refuse here so buttons can't fight the route.
            return Response(
                {"ok": False,
                 "error": "robot is driving a route autonomously — "
                          "cancel the route before teleoperating"},
                status=http_status.HTTP_409_CONFLICT)
        serializer = VelocityCommandSerializer(
            data=request.data if isinstance(request.data, dict) else {})
        if not serializer.is_valid():
            return Response({"ok": False, "error": "invalid command",
                             "errors": serializer.errors},
                            status=http_status.HTTP_400_BAD_REQUEST)
        vx = serializer.validated_data["vx"]
        wz = serializer.validated_data["wz"]
        clamped = hub.set_cmd(vx, wz)
        result = CommandResultSerializer({
            "ok": True,
            "vx": clamped[0],
            "wz": clamped[1],
            "clamped": clamped != (vx, wz),
        })
        return Response(result.data)

    @action(detail=True, methods=["get"])
    def scan(self, request, pk=None):
        """GET /api/robot/<id>/scan — newest LiDAR ring (packed LaserScan).
        Browser polls this (~2-5 Hz); 0.0 ranges mean 'no return' (gap)."""
        hub, err = self._hub_or_404(pk)
        if err:
            return err
        scan, ts = hub.scan_now()
        now = time.time()
        return Response({"ok": True,
                         "age": (now - ts) if ts else None,
                         "fresh": bool(scan) and (now - ts) < SCAN_FRESH,
                         "scan": scan})

    @action(detail=True, methods=["get", "post"])
    def nav(self, request, pk=None):
        """The nav remote (docs/NAV.md).

        GET  -> {nav state, mode, waypoints{}, maps[]}
        POST -> validated nav action queued into the hub mailbox. Actions
        reach the robot through the SAME cmd plane it already long-polls;
        only 'route' can produce motion, and only from stored waypoints."""
        hub, err = self._hub_or_404(pk)
        if err:
            return err
        assert hub is not None and pk is not None   # guaranteed by _hub_or_404
        if request.method.lower() == "get":
            return Response({"ok": True,
                             "mode": hub.nav_mode,
                             "nav": hub.nav_state,
                             "waypoints": navstore.list_waypoints(pk),
                             "maps": navstore.list_maps(pk)})
        if not registry.owns:
            return Response({"ok": False, "error": NOT_OWNER},
                            status=http_status.HTTP_503_SERVICE_UNAVAILABLE)
        ser = NavActionSerializer(data=request.data
                                  if isinstance(request.data, dict) else {})
        if not ser.is_valid():
            return Response({"ok": False, "error": "invalid nav action",
                             "errors": ser.errors},
                            status=http_status.HTTP_400_BAD_REQUEST)
        a = ser.validated_data["action"]
        name = ser.validated_data.get("name")
        map_name = ser.validated_data.get("map")
        names = ser.validated_data.get("waypoints") or []

        if hub.nav_mode == "route" and a not in ("nav_cancel", "nav_off"):
            return Response({"ok": False,
                             "error": "route running — only nav_cancel / "
                                      "nav_off accepted"},
                            status=http_status.HTTP_409_CONFLICT)

        if a == "route":
            wps = navstore.list_waypoints(pk)
            missing = [w for w in names if w not in wps]
            if missing:
                return Response({"ok": False, "error": "unknown waypoints",
                                 "missing": missing},
                                status=http_status.HTTP_400_BAD_REQUEST)
            poses = [[wps[w]["x"], wps[w]["y"], wps[w]["yaw"]] for w in names]
            rid = hub.push_nav("route", poses=poses, order=list(names))
        elif a == "align":
            wps = navstore.list_waypoints(pk)
            wp = wps.get(names[0])
            if wp is None:
                return Response({"ok": False, "error": "unknown waypoint"},
                                status=http_status.HTTP_400_BAD_REQUEST)
            rid = hub.push_nav("align", align_pose=[wp["x"], wp["y"], wp["yaw"]])
        elif a == "record":
            name = name or time.strftime("wp-%H%M%S")
            rid = hub.push_nav("record", name=name)
            hub.expect_waypoint(rid, name)   # telemetry lands as this name
        elif a in ("delete_map", "delete_waypoint"):
            if a == "delete_map":
                gone = navstore.delete_map(pk, map_name)
            else:
                gone = navstore.delete_waypoint(pk, names[0])
            if not gone:
                return Response({"ok": False, "error": "nothing deleted"},
                                status=http_status.HTTP_404_NOT_FOUND)
            rid = None
        elif a == "map_save":
            rid = hub.push_nav("map_save", name=name or "room1")
        elif a == "nav_on":
            rid = hub.push_nav("nav_on", map=map_name)
        else:                                  # map_on|map_off|nav_cancel|nav_off
            rid = hub.push_nav(a)
        return Response({"ok": True, "action": a, "rid": rid})

    @action(detail=True, methods=["post"])
    def stop(self, request, pk=None):
        """POST /api/robot/<id>/stop — zero velocity AND clear latched e-stop."""
        hub, err = self._hub_or_404(pk)
        if err:
            return err
        if not registry.owns:
            return Response({"ok": False, "error": NOT_OWNER},
                            status=http_status.HTTP_503_SERVICE_UNAVAILABLE)
        hub.stop()
        return Response({"ok": True})

    @action(detail=True, methods=["post"])
    def estop(self, request, pk=None):
        """POST /api/robot/<id>/estop — latch zero until an explicit new command."""
        hub, err = self._hub_or_404(pk)
        if err:
            return err
        if not registry.owns:
            return Response({"ok": False, "error": NOT_OWNER},
                            status=http_status.HTTP_503_SERVICE_UNAVAILABLE)
        hub.emergency_stop()
        return Response({"ok": True, "estop": True})
