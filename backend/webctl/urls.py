from django.urls import path, re_path
from rest_framework.routers import SimpleRouter

from robot import robotctl, views
from robot.viewsets import RobotViewSet

router = SimpleRouter(trailing_slash=False)
router.register("api/robot", RobotViewSet, basename="robot")
# robot ids are validated to [A-Za-z0-9_-]+ at settings parse, so the default
# DRF lookup pattern ([^/.]+) matches them all.

urlpatterns = [
    # browser-facing (Basic Auth middleware)
    path("cam/<str:robot_id>", views.cam_view, name="cam"),
    *router.urls,   # /api/robot, /api/robot/<id>, /api/robot/<id>/cmd|stop|estop
    # robot-facing (per-robot token auth inside the views; middleware skips these)
    path("robotctl/<str:robot_id>/cmd", robotctl.cmd_poll, name="robotctl-cmd"),
    path("robotctl/<str:robot_id>/telemetry", robotctl.telemetry, name="robotctl-telemetry"),
    path("robotctl/<str:robot_id>/frame", robotctl.frame, name="robotctl-frame"),
    path("robotctl/<str:robot_id>/scan", robotctl.scan, name="robotctl-scan"),
    # SPA catch-all (assets themselves are served by WhiteNoise from frontend/dist)
    re_path(r"^(?!api/|cam/|robotctl/).*$", views.index_view, name="index"),
]
