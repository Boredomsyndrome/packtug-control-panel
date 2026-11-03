"""Per-robot server state for the pull-mode architecture.

The robots are pure HTTP clients: they long-poll for commands and POST
telemetry/camera frames. Nothing in the server talks TO a robot — all state
lives here, thread-safe, one RobotHub per registered robot.

Safety model (v1 invariants preserved, watchdog now two-layered):
  * server clamps every velocity to MAX_LIN / MAX_ROT (never trust client)
  * server serves ZERO once the held command is older than CMD_TIMEOUT
    (command freshness is enforced at serve time, so a dead browser stops
    the robot via its next poll)
  * e-stop latches: effective command stays zero until /stop or a fresh cmd
  * client-side watchdog additionally zeroes the robot if the server itself
    disappears (see robotctl/client.py)
  * exactly one server process may serve the API (flock); others answer 503
"""
import errno
import fcntl
import os
import threading
import time

ONLINE_TIMEOUT = 2.0        # no poll for this long -> robot shown OFFLINE
FRAME_FRESH = 2.0           # camera frame older than this -> "no signal"
SCAN_FRESH = 3.0            # lidar scan older than this -> stale (4 Hz uplink)
_WAKE = 0.2                 # max Condition block before re-checking expiry


class RobotHub:
    """Command mailbox + telemetry + camera relay for ONE robot.

    Two transports share this one state machine:
      * HTTP  (threadpool views: long-poll GET / POST telemetry|frame)
      * WS    (async consumers in robot/ws.py: cmd pushed, telemetry+frames in)
    Thread/loop bridge: the ASGI event loop registers asyncio Events here;
    the (thread-based) mutators wake them via call_soon_threadsafe.
    """

    def __init__(self, robot_id, max_lin, max_rot, cmd_timeout):
        self.robot_id = robot_id
        self.max_lin = max_lin
        self.max_rot = max_rot
        self.cmd_timeout = cmd_timeout

        self.cv = threading.Condition()   # guards all attrs below
        self.seq = 0                      # command generation counter
        self.cmd = (0.0, 0.0)             # held (already clamped) command
        self.cmd_ts = 0.0
        self.estop = False

        self.last_poll = 0.0              # last robot contact (poll, POST or ws)
        self.battery_v = None
        self.battery_ts = 0.0
        self.applied = (0.0, 0.0)         # what the robot says it applied
        self.watchdog_trips = 0
        self.last_telemetry = None        # raw dict of last telemetry POST
        self.transport = None             # "ws" | "http" | None
        self.ws_connected = False
        self.ws_epoch = 0                 # bumped per robot ws accept (takeover)

        self.frame = None                 # latest JPEG camera frame
        self.frame_ts = 0.0
        self.frame_seq = 0

        self.scan = None                  # latest packed LaserScan (dict)
        self.scan_ts = 0.0
        self.last_cam_ok = False          # robot-side self-flags (telemetry)
        self.last_scan_ok = False
        self.ros_topics = {}              # robot's live graph (diagnostics)

        self.started = time.time()

        # --- asyncio bridge (set by asgi lifespan; empty under runserver) ---
        self._loop = None
        self._cmd_events = set()          # asyncio.Event: cmd/estop changed
        self._frame_events = set()        # asyncio.Event: new camera frame

    def set_event_loop(self, loop):
        self._loop = loop

    def add_cmd_listener(self, ev):
        self._cmd_events.add(ev)
        return lambda: self._cmd_events.discard(ev)

    def add_frame_listener(self, ev):
        self._frame_events.add(ev)
        return lambda: self._frame_events.discard(ev)

    def _wake(self, events):
        loop = self._loop
        if loop is None or not events:
            return
        for ev in list(events):
            try:
                loop.call_soon_threadsafe(ev.set)
            except RuntimeError:
                pass                      # loop closed: listener already gone


    # ---------- browser-side actions ----------

    def set_cmd(self, vx, wz):
        """Store a fresh (clamped) velocity command; clears latched e-stop."""
        vx = max(-self.max_lin, min(self.max_lin, float(vx)))
        wz = max(-self.max_rot, min(self.max_rot, float(wz)))
        with self.cv:
            self.cmd = (vx, wz)
            self.cmd_ts = time.time()
            self.estop = False
            self.seq += 1
            self.cv.notify_all()
        self._wake(self._cmd_events)
        return vx, wz

    def stop(self):
        """Immediate stop AND clear latched e-stop."""
        with self.cv:
            self.cmd = (0.0, 0.0)
            self.cmd_ts = 0.0
            self.estop = False
            self.seq += 1
            self.cv.notify_all()
        self._wake(self._cmd_events)

    def emergency_stop(self):
        """Zero and latch: serve zero until /stop or an explicit new cmd."""
        with self.cv:
            self.cmd = (0.0, 0.0)
            self.cmd_ts = 0.0
            self.estop = True
            self.seq += 1
            self.cv.notify_all()
        self._wake(self._cmd_events)

    # ---------- robot-facing actions ----------

    def poll(self, last_seq, wait):
        """Long-poll: block (up to `wait` s) until the command generation
        changes, then return the effective command. Marks the robot online.

        Staleness is checked before every return, so a response NEVER carries
        a command older than cmd_timeout — the robot may apply it blindly."""
        deadline = time.monotonic() + max(0.0, min(wait, 1.0))
        with self.cv:
            self.last_poll = time.time()
            while True:
                self._expire_locked()
                if last_seq is None or self.seq != last_seq:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.cv.wait(min(_WAKE, remaining))
            snap = self._snapshot_locked()
        self._wake(self._cmd_events)   # expiry inside may have bumped seq
        return snap

    def effective(self):
        """Effective command right now (expiry applied) — shared by the WS
        cmd pump and status(); NEVER returns a cmd older than cmd_timeout."""
        with self.cv:
            self._expire_locked()
            return self._snapshot_locked()

    def _snapshot_locked(self):
        vx, wz = (0.0, 0.0) if self.estop else self.cmd
        return {
            "vx": vx, "wz": wz,
            "estop": self.estop,
            "seq": self.seq,
            "cmd_timeout": self.cmd_timeout,
        }

    def contact(self, transport):
        with self.cv:
            self.last_poll = time.time()
            self.transport = transport

    def set_ws_connected(self, on, epoch=None):
        with self.cv:
            self.ws_connected = on
            self.transport = "ws" if on else (self.transport if not on else None)
            if on:
                self.last_poll = time.time()
                self.ws_epoch = epoch if epoch is not None else self.ws_epoch + 1
                return self.ws_epoch
            return None

    def take_ws_slot(self):
        with self.cv:
            self.ws_epoch += 1
            return self.ws_epoch

    def set_telemetry(self, data, transport="http"):
        with self.cv:
            self.last_poll = time.time()
            if not self.ws_connected:
                self.transport = transport
            self.last_telemetry = data
            v = data.get("battery_v")
            if isinstance(v, (int, float)):
                self.battery_v = float(v)
                self.battery_ts = time.time()
            ax, az = data.get("applied_vx"), data.get("applied_wz")
            if isinstance(ax, (int, float)) and isinstance(az, (int, float)):
                self.applied = (float(ax), float(az))
            w = data.get("watchdog_trips")
            if isinstance(w, int):
                self.watchdog_trips = w
            self.last_cam_ok = bool(data.get("cam_ok"))
            self.last_scan_ok = bool(data.get("scan_ok"))
            rt = data.get("ros_topics")
            if isinstance(rt, dict):
                self.ros_topics = rt

    def set_scan(self, scan):
        """Store the newest packed LaserScan (pre-validated dict)."""
        with self.cv:
            self.scan = scan
            self.scan_ts = time.time()
            self.last_poll = time.time()

    def scan_now(self):
        with self.cv:
            return self.scan, self.scan_ts

    def set_frame(self, jpeg):
        with self.cv:
            self.frame = jpeg
            self.frame_ts = time.time()
            self.frame_seq += 1
            self.cv.notify_all()
            frame_seq = self.frame_seq
        self._wake(self._frame_events)
        return frame_seq

    def frame_now(self):
        with self.cv:
            return self.frame, self.frame_seq

    def next_frame(self, seen_seq, timeout=1.0):
        """Block until a camera frame newer than `seen_seq` arrives."""
        deadline = time.monotonic() + timeout
        with self.cv:
            while self.frame_seq <= seen_seq:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None, self.frame_seq
                self.cv.wait(min(_WAKE, remaining))
            return self.frame, self.frame_seq

    # ---------- shared helpers ----------

    def _expire_locked(self):
        """Zero a held command whose freshness lapsed (caller holds cv).
        Bumps seq so waiting robots pick the zero up immediately."""
        if self.cmd != (0.0, 0.0) and (time.time() - self.cmd_ts) > self.cmd_timeout:
            self.cmd = (0.0, 0.0)
            self.seq += 1

    def status(self):
        now = time.time()
        with self.cv:
            vx, wz = (0.0, 0.0) if self.estop else self.cmd
            return {
                "id": self.robot_id,
                "online": (now - self.last_poll) < ONLINE_TIMEOUT if self.last_poll else False,
                "battery_v": self.battery_v,
                "battery_age": (now - self.battery_ts) if self.battery_ts else None,
                "cmd": {"vx": vx, "wz": wz},
                "cmd_age": (now - self.cmd_ts) if self.cmd_ts else None,
                "applied": {"vx": self.applied[0], "wz": self.applied[1]},
                "estop": self.estop,
                "watchdog_trips": self.watchdog_trips,
                "camera": self.frame is not None and (now - self.frame_ts) < FRAME_FRESH,
                "scan": self.scan is not None and (now - self.scan_ts) < SCAN_FRESH,
                "scan_age": (now - self.scan_ts) if self.scan_ts else None,
                "robot_cam_ok": self.last_cam_ok,
                "robot_scan_ok": self.last_scan_ok,
                "ros_topics": dict(self.ros_topics),
                "transport": "ws" if self.ws_connected else (
                    "http" if self.last_poll else None),
                "ws_connected": self.ws_connected,
                "cmd_timeout": self.cmd_timeout,
                "max_lin": self.max_lin,
                "max_rot": self.max_rot,
                "uptime": now - self.started,
                "seq": self.seq,
            }


class RobotRegistry:
    """All robots this server controls, plus the single-process owner guard.

    Robots authenticate per-id with their own token (HTTP Basic:
    username = robot id, password = token), so N robots share one server and
    one browser UI while never being able to impersonate each other.
    """

    def __init__(self, robots, max_lin, max_rot, cmd_timeout, lock_path=None):
        self.token_of = dict(robots)
        self.hubs = {rid: RobotHub(rid, max_lin, max_rot, cmd_timeout)
                     for rid in robots}
        self.lock_path = lock_path
        self._lock_fd = None
        self.owns = lock_path is None

    def acquire_ownership(self):
        """flock the lock file; False if another process already owns it."""
        if self._lock_fd is not None or not self.lock_path:
            self.owns = True
            return True
        fd = open(self.lock_path, "w")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EACCES):
                fd.close()
                return False
            raise
        fd.write(str(os.getpid()))
        fd.flush()
        self._lock_fd = fd
        self.owns = True
        return True

    def robot(self, robot_id):
        return self.hubs.get(robot_id)

    def check_token(self, robot_id, username, token):
        """True if username/password match this robot's registered token."""
        import hmac
        expected = self.token_of.get(robot_id)
        if expected is None:
            return False
        if expected == "":            # dev mode: empty token = auth open
            return username == robot_id
        return (hmac.compare_digest(str(username or ""), robot_id)
                and hmac.compare_digest(str(token or ""), expected))

    def summary(self):
        return [hub.status() for hub in self.hubs.values()]


# Module-level singleton (Django imports this once per process)
from django.conf import settings

registry = RobotRegistry(
    robots=settings.WEBCTL_ROBOTS,
    max_lin=settings.MAX_LIN,
    max_rot=settings.MAX_ROT,
    cmd_timeout=settings.CMD_TIMEOUT,
    lock_path=str(settings.PROJECT_ROOT / ".server.lock"),
)
