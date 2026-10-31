#!/usr/bin/env python3
"""wheeltec-webctl ROBOT CLIENT (pull-mode v2) — runs on the robot SBC.

The robot is a PURE CLIENT: it makes only OUTBOUND HTTPS/HTTP calls to the
control server and listens on NO ports (no rosbridge, no web_video_server
reachable, no SSH needed). One process = one robot; run one per robot.

    server  <── GET  /robotctl/<id>/cmd        long-poll for the effective cmd
            <── POST /robotctl/<id>/telemetry  battery + what we applied
            <── POST /robotctl/<id>/frame      camera JPEGs (optional)

Safety (two layers, per the brushless rules):
  * this client publishes the last polled velocity to /cmd_vel at 10 Hz;
  * if no poll has SUCCEEDED within WEBCTL_WATCHDOG seconds (server dead,
    WiFi dead, server frozen...) it publishes ZERO regardless of what it
    last held — the link dies, the robot stops, full stop;
  * the server never serves a command older than CMD_TIMEOUT or during a
    latched e-stop, so anything the client applies is fresh by construction.

Environment (see robotctl/README or ROBOT_SETUP.md):
  WEBCTL_SERVER=http://<server-lan-ip>:8000   required
  WEBCTL_ROBOT_ID / WEBCTL_ROBOT_TOKEN        required (must match server registry)
  WEBCTL_WATCHDOG=1.0                         link-loss -> stop seconds
  WEBCTL_POLL_WAIT=0.4                        long-poll seconds per request
  WEBCTL_CAM_URL=http://127.0.0.1:8080/stream?topic=/camera/image_raw
                                              OPTIONAL local MJPEG source
  WEBCTL_CMD_VEL_TOPIC=/cmd_vel
  WEBCTL_BATTERY_TOPICS=/PowerVoltage,/power_voltage
  WEBCTL_DRY_RUN=1                            no ROS needed (integration tests)
  WEBCTL_SINK=/tmp/webctl-sink.json           dry-run: latest applied state
"""
import json
import logging
import os
import signal
import sys
import threading
import time

import requests

log = logging.getLogger("robotctl")

SERVER = os.getenv("WEBCTL_SERVER", "http://127.0.0.1:8000").rstrip("/")
ROBOT_ID = os.getenv("WEBCTL_ROBOT_ID", "")
ROBOT_TOKEN = os.getenv("WEBCTL_ROBOT_TOKEN", "")
WATCHDOG = float(os.getenv("WEBCTL_WATCHDOG", "1.0"))
POLL_WAIT = float(os.getenv("WEBCTL_POLL_WAIT", "0.4"))
CAM_URL = os.getenv("WEBCTL_CAM_URL", "")
CMD_VEL_TOPIC = os.getenv("WEBCTL_CMD_VEL_TOPIC", "/cmd_vel")
BATTERY_TOPICS = [t.strip() for t in
                  os.getenv("WEBCTL_BATTERY_TOPICS", "/PowerVoltage,/power_voltage").split(",")
                  if t.strip()]
DRY_RUN = os.getenv("WEBCTL_DRY_RUN", "").lower() in ("1", "true", "yes")
SINK = os.getenv("WEBCTL_SINK", "")
DRY_BATTERY = float(os.getenv("WEBCTL_DRY_BATTERY", "24.3"))
PUBLISH_HZ = 10.0

running = True


def _sig(_s, _f):
    global running
    running = False


signal.signal(signal.SIGTERM, _sig)
signal.signal(signal.SIGINT, _sig)


class Sink:
    """Dry-run velocity sink: the integration harness reads this file to
    assert exactly what a real robot WOULD have received."""

    def __init__(self, path):
        self.path = path
        self.last_write = 0.0

    def publish(self, vx, wz, state):
        if time.monotonic() - self.last_write < 0.25:
            return
        self.last_write = time.monotonic()
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"vx": vx, "wz": wz, **state}, fh)
        os.replace(tmp, self.path)


class RosSink:
    """Real robot: rclpy node with /cmd_vel publisher + battery subscribers."""

    def __init__(self):
        import rclpy
        from geometry_msgs.msg import Twist
        from std_msgs.msg import Float32
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.qos import QoSDurabilityPolicy, QoSPresetProfiles
        from rclpy.qos import QoSProfile

        self._Twist = Twist
        rclpy.init()
        self.node = rclpy.create_node("webctl_robot_client")
        self.pub = self.node.create_publisher(Twist, CMD_VEL_TOPIC, 10)
        self.battery_v = None
        qos = QoSProfile(depth=1, durability=QoSDurabilityPolicy.RELIABLE)
        for topic in BATTERY_TOPICS:
            try:
                self.node.create_subscription(
                    Float32, topic, lambda m, t=topic: self._batt(m, t), qos)
            except Exception as e:  # odd image variants may name things oddly
                log.warning("battery topic %s unavailable: %s", topic, e)
        ex = SingleThreadedExecutor()
        ex.add_node(self.node)
        threading.Thread(target=ex.spin, daemon=True, name="rclpy-spin").start()

    def _batt(self, msg, topic):
        self.battery_v = float(msg.data)

    def publish(self, vx, wz, state):
        m = self._Twist()
        m.linear.x, m.linear.y, m.linear.z = vx, 0.0, 0.0
        m.angular.x, m.angular.y, m.angular.z = 0.0, 0.0, wz
        self.pub.publish(m)


def make_sink():
    if not DRY_RUN:
        return RosSink()
    if not SINK:
        log.warning("WEBCTL_SINK not set; dry-run velocities go nowhere")

        class _Null(Sink):
            def __init__(self):
                super().__init__("/dev/null")
        return _Null()
    return Sink(SINK)


class Client:
    def __init__(self):
        if not ROBOT_ID:
            sys.exit("WEBCTL_ROBOT_ID is required")
        if not SERVER:
            sys.exit("WEBCTL_SERVER is required")
        self.sess = requests.Session()
        self.sess.auth = (ROBOT_ID, ROBOT_TOKEN)
        self.lock = threading.Lock()
        self.held = (0.0, 0.0)          # velocity from the newest server reply
        self.last_ok = 0.0              # monotonic time of last successful poll
        self.polled_once = False        # before the FIRST success the
        self.tripped = False            #   watchdog must not "trip" (we never
        self.trips = 0                  #   had anything to protect)
        self.publishes = 0
        self.state = "starting"
        self.sink = make_sink()
        self.cam_stop = threading.Event()

    # ---------- velocity ----------

    def apply(self, vx, wz):
        with self.lock:
            self.held = (float(vx), float(wz))
            self.last_ok = time.monotonic()
            self.polled_once = True
            self.tripped = False
            self.state = "driven"

    def effective(self):
        """Watchdog: no successful poll within WATCHDOG s -> forced zero."""
        with self.lock:
            vx, wz = self.held
            if not self.polled_once:
                return 0.0, 0.0     # never talked to the server: stay parked
            ok_age = time.monotonic() - self.last_ok
        if ok_age > WATCHDOG:
            if not self.tripped:
                self.trips += 1
                self.tripped = True
                log.warning("watchdog TRIPPED (no poll for %.2fs) -> zero", ok_age)
            self.state = "watchdog"
            return 0.0, 0.0
        return vx, wz

    # ---------- threads ----------

    def fetch_loop(self):
        seq = None
        url = f"{SERVER}/robotctl/{ROBOT_ID}/cmd"
        while running:
            params = {"wait": POLL_WAIT}
            if seq is not None:
                params["seq"] = seq
            try:
                r = self.sess.get(url, params=params,
                                  timeout=(5.0, POLL_WAIT + 3.0))
                r.raise_for_status()
                d = r.json()
                seq = int(d["seq"])
                self.apply(d["vx"], d["wz"])
                if d.get("estop"):
                    self.state = "estop"
            except Exception as e:
                self.state = f"link error: {type(e).__name__}"
                log.warning("cmd poll failed: %s", e)
                time.sleep(0.2)

    def publish_loop(self):
        dt = 1.0 / PUBLISH_HZ
        while running:
            vx, wz = self.effective()
            try:
                self.sink.publish(vx, wz, {
                    "publishes": self.publishes,
                    "watchdog_trips": self.trips,
                    "state": self.state,
                })
            except Exception as e:
                log.error("velocity publish failed: %s", e)
            self.publishes += 1
            time.sleep(dt)
        # final rest: keep the base seeing zeros
        for _ in range(5):
            try:
                self.sink.publish(0.0, 0.0, {"publishes": self.publishes,
                                             "watchdog_trips": self.trips,
                                             "state": "shutdown"})
            except Exception:
                pass
            time.sleep(0.1)

    def battery_v(self):
        if DRY_RUN:
            return DRY_BATTERY
        return getattr(self.sink, "battery_v", None)

    def telemetry_loop(self):
        url = f"{SERVER}/robotctl/{ROBOT_ID}/telemetry"
        while running:
            vx, wz = self.effective()
            body = {"battery_v": self.battery_v(),
                    "applied_vx": vx, "applied_wz": wz,
                    "watchdog_trips": self.trips}
            try:
                r = self.sess.post(url, json=body, timeout=5)
                if r.status_code >= 400:
                    log.warning("telemetry rejected (%s): %s", r.status_code, r.text[:200])
            except Exception as e:
                log.debug("telemetry failed: %s", e)
            time.sleep(1.0)

    def cam_loop(self):
        """Ingest a LOCAL MJPEG source (e.g. web_video_server bound to
        localhost) and push each JPEG up. SOI/EOI scan is boundary-agnostic
        and safe: 0xFF is byte-stuffed inside JPEG payload data, so FFD9
        only ever marks a real end-of-image. Upload failures drop frames —
        driving is never blocked by the camera."""
        url = f"{SERVER}/robotctl/{ROBOT_ID}/frame"
        while running and not self.cam_stop.is_set():
            try:
                with self.sess.get(CAM_URL, stream=True, timeout=15,
                                   headers={"Accept": "multipart/x-mixed-replace"}) as r:
                    r.raise_for_status()
                    buf = b""
                    for chunk in r.iter_content(8192):
                        if self.cam_stop.is_set() or not running:
                            return
                        buf += chunk
                        while True:
                            soi = buf.find(b"\xff\xd8")
                            if soi < 0:
                                buf = b""
                                break
                            eoi = buf.find(b"\xff\xd9", soi + 4)
                            if eoi < 0:
                                buf = buf[soi:][: 8 * 1024 * 1024]  # cap partial
                                break
                            jpeg = buf[soi:eoi + 2]
                            buf = buf[eoi + 2:]
                            try:
                                self.sess.post(url, data=jpeg, timeout=5,
                                               headers={"Content-Type": "image/jpeg"})
                            except Exception:
                                time.sleep(0.3)   # server blip; keep driving on
            except Exception as e:
                log.warning("camera ingest failed (%s), retrying in 2s", e)
                time.sleep(2.0)

    # ---------- main ----------

    def run(self):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        log.info("robotctl client '%s' -> %s (dry_run=%s watchdog=%.2fs cam=%s)",
                 ROBOT_ID, SERVER, DRY_RUN, WATCHDOG, CAM_URL or "off")
        threads = [
            threading.Thread(target=self.fetch_loop, daemon=True, name="fetch"),
            threading.Thread(target=self.publish_loop, daemon=True, name="publish"),
            threading.Thread(target=self.telemetry_loop, daemon=True, name="telemetry"),
        ]
        if CAM_URL:
            threads.append(threading.Thread(target=self.cam_loop, daemon=True, name="cam"))
        for t in threads:
            t.start()
        while running:
            time.sleep(0.2)
        log.info("robotctl client shutting down")


if __name__ == "__main__":
    Client().run()
