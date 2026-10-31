import os
import sys

from django.apps import AppConfig


class RobotConfig(AppConfig):
    name = "robot"

    def ready(self):
        # Guard: `manage.py runserver` without --noreload starts TWO processes
        # (reloader parent + real server). Only the child (WERKZEUG_RUN_MAIN=true)
        # may own the hub state. The .server.lock flock in
        # RobotRegistry.acquire_ownership() is the hard backstop against
        # duplicate workers on any WSGI server — with in-memory hub state,
        # TWO live processes would each hold half the fleet's state, so a
        # non-owner process must never serve state (it answers 503).
        argv1 = sys.argv[1] if len(sys.argv) > 1 else ""
        is_reloader_parent = (
            argv1 == "runserver"
            and "--noreload" not in sys.argv
            and os.environ.get("WERKZEUG_RUN_MAIN") != "true"
        )
        management_only = argv1 not in ("runserver",) and "gunicorn" not in " ".join(sys.argv)
        if is_reloader_parent or management_only:
            return
        from robot.hub import registry
        if not registry.acquire_ownership():
            import logging
            logging.getLogger("robot").error(
                "another process already owns the robot hub state; "
                "run exactly ONE server process (all its API calls will 503)")
