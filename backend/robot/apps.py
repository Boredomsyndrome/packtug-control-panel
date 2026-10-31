import os
import sys

from django.apps import AppConfig


class RobotConfig(AppConfig):
    name = "robot"

    def ready(self):
        # Guard: `manage.py runserver` without --noreload starts TWO processes
        # (reloader parent + real server). Only the child (WERKZEUG_RUN_MAIN=true)
        # runs the bridge. The .bridge.lock flock in RobotBridge.start() is the
        # hard backstop against duplicate workers on any WSGI server.
        argv1 = sys.argv[1] if len(sys.argv) > 1 else ""
        is_reloader_parent = (
            argv1 == "runserver"
            and "--noreload" not in sys.argv
            and os.environ.get("WERKZEUG_RUN_MAIN") != "true"
        )
        management_only = argv1 not in ("runserver",) and "gunicorn" not in " ".join(sys.argv)
        if is_reloader_parent or management_only:
            return
        from robot.bridge import bridge
        bridge.start()
