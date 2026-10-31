from django.urls import path, re_path
from rest_framework.routers import SimpleRouter

from robot import views
from robot.viewsets import RobotViewSet

router = SimpleRouter(trailing_slash=False)
router.register("api/robot", RobotViewSet, basename="robot")

urlpatterns = [
    path("cam", views.cam_view, name="cam"),
    *router.urls,   # /api/robot, /api/robot/cmd, /api/robot/stop, /api/robot/estop
    # SPA catch-all (assets themselves are served by WhiteNoise from frontend/dist)
    re_path(r"^(?!api/|cam$).*$", views.index_view, name="index"),
]
