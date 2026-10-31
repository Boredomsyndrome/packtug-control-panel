"""Persistent rosbridge WebSocket client with a safety watchdog.

One RobotBridge instance lives inside the Django process. It:
  * keeps a reconnecting WebSocket connection to rosbridge_server on the robot,
  * subscribes to battery telemetry,
  * re-publishes the latest /cmd_vel Twist at 10 Hz,
  * publishes ZERO Twist whenever no fresh command arrived within CMD_TIMEOUT
    seconds (watchdog) or an emergency stop latched.

All velocity values are clamped to MAX_LIN / MAX_ROT before publishing.
"""
import asyncio
import errno
import fcntl
import json
import logging
import threading
import time

import websockets

log = logging.getLogger("robot.bridge")

BATTERY_CANDIDATES = ["/PowerVoltage", "/power_voltage"]  # both wheeltec image variants
PUBLISH_HZ = 10.0


class RobotBridge:
    def __init__(self, ws_url, battery_topic="", cmd_vel_topic="/cmd_vel",
                 max_lin=0.5, max_rot=0.7, cmd_timeout=0.5, lock_path=None):
        self.ws_url = ws_url
        self.battery_topics = [battery_topic] if battery_topic else BATTERY_CANDIDATES
        self.cmd_vel_topic = cmd_vel_topic
        self.max_lin = max_lin
        self.max_rot = max_rot
        self.cmd_timeout = cmd_timeout
        self.lock_path = lock_path

        self._lock = threading.Lock()
        self._cmd_vx = 0.0
        self._cmd_wz = 0.0
        self._last_cmd_time = 0.0
        self._estop_latched = False
        self._connected = False
        self._battery_v = None
        self._battery_topic = None
        self._battery_ts = 0.0
        self._started = time.time()
        self._publish_count = 0
        self._error = None
        self._thread = None
        self._lock_fd = None

    # ---------- public API (called from Django views) ----------

    def start(self):
        """Acquire the singleton lock and start the bridge thread.

        Returns True if this process now owns the bridge, False if another
        process already owns it (guard against duplicate dev-server/gunicorn
        workers, which would fight each other over /cmd_vel).
        """
        if self._thread is not None:
            return True
        if self.lock_path:
            self._lock_fd = open(self.lock_path, "w")
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as e:
                if e.errno in (errno.EAGAIN, errno.EACCES):
                    log.error("another process already owns the robot bridge; "
                              "run exactly ONE server worker")
                    self._lock_fd.close()
                    self._lock_fd = None
                    return False
                raise
            self._lock_fd.write(str(__import__("os").getpid()))
            self._lock_fd.flush()
        self._thread = threading.Thread(target=self._main, daemon=True, name="robot-bridge")
        self._thread.start()
        return True

    @property
    def owns_bridge(self):
        return self._thread is not None

    def set_cmd(self, vx, wz):
        """Store a fresh velocity command (clamped). Clears latched e-stop."""
        vx = max(-self.max_lin, min(self.max_lin, float(vx)))
        wz = max(-self.max_rot, min(self.max_rot, float(wz)))
        with self._lock:
            self._cmd_vx, self._cmd_wz = vx, wz
            self._last_cmd_time = time.time()
            self._estop_latched = False
        return vx, wz

    def stop(self):
        """Explicit immediate stop (unlatches e-stop, zeroes velocity)."""
        with self._lock:
            self._cmd_vx = self._cmd_wz = 0.0
            self._last_cmd_time = 0.0
            self._estop_latched = False

    def emergency_stop(self):
        """Zero velocity and latch: stay stopped until a fresh set_cmd()."""
        with self._lock:
            self._cmd_vx = self._cmd_wz = 0.0
            self._estop_latched = True

    def status(self):
        with self._lock:
            return {
                "connected": self._connected,
                "ws_url": self.ws_url,
                "battery_v": self._battery_v,
                "battery_topic": self._battery_topic,
                "battery_age": (time.time() - self._battery_ts) if self._battery_ts else None,
                "cmd": {"vx": self._cmd_vx, "wz": self._cmd_wz},
                "cmd_age": (time.time() - self._last_cmd_time) if self._last_cmd_time else None,
                "estop": self._estop_latched,
                "cmd_timeout": self.cmd_timeout,
                "uptime": time.time() - self._started,
                "publishes": self._publish_count,
                "last_error": self._error,
                "max_lin": self.max_lin,
                "max_rot": self.max_rot,
                "owns_bridge": self.owns_bridge,
            }

    # ---------- internal asyncio loop ----------

    def _main(self):
        try:
            asyncio.run(self._run())
        except Exception as e:  # pragma: no cover
            log.exception("bridge thread died: %s", e)
            with self._lock:
                self._error = f"bridge thread died: {e}"

    async def _run(self):
        dt = 1.0 / PUBLISH_HZ
        while True:
            try:
                async with websockets.connect(self.ws_url, open_timeout=5,
                                              ping_interval=5, ping_timeout=5) as ws:
                    log.info("rosbridge connected: %s", self.ws_url)
                    with self._lock:
                        self._connected = True
                        self._error = None
                    for t in self.battery_topics:
                        await ws.send(json.dumps({
                            "op": "subscribe", "topic": t, "type": "std_msgs/msg/Float32",
                        }))
                    await self._session(ws, dt)
            except Exception as e:
                with self._lock:
                    self._connected = False
                    self._error = f"{type(e).__name__}: {e}"
                log.warning("rosbridge connection error: %s", e)
                await asyncio.sleep(1.0)

    async def _session(self, ws, dt):
        """Concurrent 10 Hz publisher + telemetry reader; first to fail ends
        the session (then _run reconnects)."""
        pub = asyncio.create_task(self._publish_loop(ws, dt))
        rdr = asyncio.create_task(self._read_loop(ws))
        done, pending = await asyncio.wait({pub, rdr}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if exc:
                raise exc
        raise ConnectionError("rosbridge stream ended (no recv)")

    async def _publish_loop(self, ws, dt):
        while True:
            with self._lock:
                fresh = (time.time() - self._last_cmd_time) <= self.cmd_timeout
                vx, wz = (self._cmd_vx, self._cmd_wz) if (fresh and not self._estop_latched) else (0.0, 0.0)
            msg = {
                "op": "publish",
                "topic": self.cmd_vel_topic,
                "type": "geometry_msgs/msg/Twist",
                "msg": {"linear": {"x": vx, "y": 0.0, "z": 0.0},
                        "angular": {"x": 0.0, "y": 0.0, "z": wz}},
            }
            await ws.send(json.dumps(msg))
            with self._lock:
                self._publish_count += 1
            await asyncio.sleep(dt)

    async def _read_loop(self, ws):
        while True:
            raw = await ws.recv()
            self._handle_incoming(raw)

    def _handle_incoming(self, raw):
        try:
            m = json.loads(raw)
        except (ValueError, TypeError):
            return
        if m.get("op") != "publish":
            return
        topic = m.get("topic", "")
        data = (m.get("msg") or {}).get("data")
        if isinstance(data, (int, float)):
            with self._lock:
                self._battery_v = float(data)
                self._battery_topic = topic
                self._battery_ts = time.time()


# Module-level singleton (Django imports this once per process)
from django.conf import settings

bridge = RobotBridge(
    ws_url=settings.ROBOT_WS_URL,
    battery_topic=settings.BATTERY_TOPIC,
    cmd_vel_topic=settings.CMD_VEL_TOPIC,
    max_lin=settings.MAX_LIN,
    max_rot=settings.MAX_ROT,
    cmd_timeout=settings.CMD_TIMEOUT,
    lock_path=str(settings.PROJECT_ROOT / ".bridge.lock"),
)
