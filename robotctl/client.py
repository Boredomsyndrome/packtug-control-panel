#!/usr/bin/env python3
"""wheeltec-webctl ROBOT CLIENT — runs on the robot Jetson/SBC.

ZERO INSTALLS, ZERO THIRD-PARTY DEPS: only the Python 3 standard library,
plus `rclpy` which ships in the robot's ROS2 image (and is skipped entirely
in dry-run mode). The robot has NO internet access by design.

The robot is a PURE CLIENT — it dials the server, and listens on NO ports.
Primary transport is a WebSocket (stdlib socket implementation below);
if the server has no WS (e.g. plain gunicorn/runserver) the client falls
back to the v2 HTTP long-poll endpoints automatically.

    ws  /ws/robotctl/<id>?token=…   cmd push  |  telemetry + JPEG frames in
    http /robotctl/<id>/cmd         long-poll fallback for all three

Safety (unchanged, two layers):
  * we publish the latest server velocity to /cmd_vel at 10 Hz;
  * if NO server contact for WEBCTL_WATCHDOG seconds (server died, WiFi
    died, server froze…) we publish ZERO — link down means robot stopped;
  * the server never emits a stale or e-stopped non-zero velocity, so what
    we apply is safe by construction; clamps are server-side.

Camera (all optional, auto-detected, never blocks driving):
  WEBCTL_CAM_MODE=auto|off|ros|cv2|mjpeg        default: mjpeg if CAM_URL set
  WEBCTL_CAM_ROS_TOPIC=/camera/image_raw   (rclpy; rgb8/bgr8/mono8/rgba/bgra)
  WEBCTL_CAM_DEVICE=0                       (cv2 VideoCapture index or a
                                             gstreamer pipeline string)
  WEBCTL_CAM_URL=http://127.0.0.1:8080/…    (local web_video_server MJPEG)
  WEBCTL_CAM_WIDTH=960  WEBCTL_CAM_FPS=15  WEBCTL_CAM_JPEG_Q=70
  If none of the candidate image topics ever delivers, the client DISCOVERS
  any sensor_msgs/msg/Image topic on the graph and subscribes to it — vendor
  naming differences (and camera nodes started after the client) self-heal.

LiDAR (auto, never blocks driving):
  WEBCTL_SCAN_MODE=auto|off                 default auto (synthetic in dry-run)
  WEBCTL_SCAN_TOPIC / WEBCTL_SCAN_CANDIDATES=/scan,/scan_raw,/base_scan,...
  WEBCTL_SCAN_HZ=4      upload rate (latest scan wins; small JSON)
  WEBCTL_SCAN_RAYS=180  downsample of the raw ring (0 = every ray)
  WEBCTL_SCAN_MAX_M=12  rays beyond this (or inf/nan) -> 0 = "no return"
  LSLidar M10P on the S2000: ros2 launch turn_on_wheeltec_robot
  wheeltec_lidar.launch.py  (serial /dev/wheeltec_lidar, publishes /scan)

Other env: WEBCTL_SERVER, WEBCTL_ROBOT_ID/_TOKEN, WEBCTL_TRANSPORT=auto|ws|http,
WEBCTL_WATCHDOG=2.5, WEBCTL_POLL_WAIT=0.4, WEBCTL_CMD_VEL_TOPIC,
WEBCTL_BATTERY_TOPICS, WEBCTL_DRY_RUN=1, WEBCTL_SINK=/tmp/x.json,
WEBCTL_DRY_BATTERY=24.3, WEBCTL_CLIENT_VERSION=3.3.0-stdlib

Autonomous nav (v3.3, opt-in WEBCTL_NAV=1, docs/NAV.md): navd.py (same dir)
runs the VENDOR slam_toolbox/Nav2 pipeline via ~/robotctl/navctl.sh. Nav
commands ride the existing cmd plane ("nav" field); state rides telemetry
("nav" field). While a route runs, Nav2 owns /cmd_vel and this client's
10 Hz teleop publisher is SUSPENDED; estop/watchdog/SIGTERM halt the goal
AND stop the nav stack. With WEBCTL_NAV unset everything below is inert.
"""
import base64
import http.client
import json
import logging
import os
import select
import signal
import socket
import ssl
import struct
import sys
import tempfile
import threading
import time
import types
import urllib.error
import urllib.parse
import urllib.request
from hashlib import sha1
from urllib.request import build_opener, ProxyHandler

log = logging.getLogger("robotctl")
VERSION = os.getenv("WEBCTL_CLIENT_VERSION", "3.3.0-stdlib")

SERVER = os.getenv("WEBCTL_SERVER", "http://127.0.0.1:8000").rstrip("/")
ROBOT_ID = os.getenv("WEBCTL_ROBOT_ID", "")
ROBOT_TOKEN = os.getenv("WEBCTL_ROBOT_TOKEN", "")
TRANSPORT = os.getenv("WEBCTL_TRANSPORT", "auto").lower()      # auto|ws|http
POLL_WAIT = float(os.getenv("WEBCTL_POLL_WAIT", "0.4"))
# v3.0.2: 1.0s was trigger-happy — on congested WiFi one big frame write can
# hold the socket ~1s and starve pong, tripping the watchdog while healthy.
# 2.5s still stops a dead link far inside human reaction time.
WATCHDOG = float(os.getenv("WEBCTL_WATCHDOG", "2.5"))   # noqa: E305
# server traffic only flows when commands CHANGE; the ping keeps "contact"
# alive, so it MUST stay well under WATCHDOG (pong updates last_ok)
WS_PING_S = min(float(os.getenv("WEBCTL_WS_PING_INTERVAL", "0.8")),
                WATCHDOG / 3.0)

CAM_MODE = os.getenv("WEBCTL_CAM_MODE", "mjpeg" if os.getenv("WEBCTL_CAM_URL") else "auto")
CAM_URL = os.getenv("WEBCTL_CAM_URL", "")
CAM_ROS_TOPIC = os.getenv("WEBCTL_CAM_ROS_TOPIC", "")
CAM_DEVICE = os.getenv("WEBCTL_CAM_DEVICE", "")
CAM_WIDTH = int(os.getenv("WEBCTL_CAM_WIDTH", "960"))
CAM_FPS = float(os.getenv("WEBCTL_CAM_FPS", "15"))
CAM_JPEG_Q = int(os.getenv("WEBCTL_CAM_JPEG_Q", "70"))
ROS_CAM_CANDIDATES = [t for t in os.getenv(
    "WEBCTL_CAM_ROS_CANDIDATES",
    "/camera/image_raw,/camera/color/image_raw,/usb_cam/image_raw,/image_raw").split(",") if t]

SCAN_MODE = os.getenv("WEBCTL_SCAN_MODE", "auto").lower()          # auto|off
SCAN_TOPIC = os.getenv("WEBCTL_SCAN_TOPIC", "")
SCAN_CANDIDATES = [t for t in os.getenv(
    "WEBCTL_SCAN_CANDIDATES", "/scan,/scan_raw,/base_scan,/scan_filtered").split(",") if t]
SCAN_HZ = float(os.getenv("WEBCTL_SCAN_HZ", "4"))
SCAN_RAYS = int(os.getenv("WEBCTL_SCAN_RAYS", "180"))
SCAN_MAX_M = float(os.getenv("WEBCTL_SCAN_MAX_M", "12.0"))
SCAN_DISCOVER_S = float(os.getenv("WEBCTL_SCAN_DISCOVER_S", "6.0"))

CMD_VEL_TOPIC = os.getenv("WEBCTL_CMD_VEL_TOPIC", "/cmd_vel")
# v3.3 autonomous nav helper (docs/NAV.md). When this flag is off NOTHING
# related is imported — client behaviour is exactly the v3.2 teleop stack.
NAV_ON = os.getenv("WEBCTL_NAV", "").lower() in ("1", "true", "yes")
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


# ---------------------------------------------------------------- websocket
class WSError(Exception):
    pass


class StdWs:
    """Minimal RFC6455 WebSocket CLIENT — stdlib only (the robot installs
    nothing). Client frames are masked, server frames accepted unmasked;
    handles fragmentation, ping (auto-pong), close, text+binary."""

    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    FIN, CONT, TEXT, BIN, CLOSE, PING, PONG = 0x80, 0x0, 0x1, 0x2, 0x8, 0x9, 0xA

    def __init__(self, url, timeout=5.0):
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("ws", "wss"):
            raise WSError(f"scheme {u.scheme!r} unsupported")
        self.secure = u.scheme == "wss"
        self.host = u.hostname
        self.port = u.port or (443 if self.secure else 80)
        self.path = (u.path or "/") + (("?" + u.query) if u.query else "")
        self.timeout = timeout
        self._send_lock = threading.Lock()
        self._rx = b""
        self._frag = bytearray()
        self._frag_op = None
        self.sock = None

    def connect(self):
        try:
            s = socket.create_connection((self.host, self.port), self.timeout)
        except OSError as e:
            raise WSError(f"tcp connect: {e}") from e
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass
        if self.secure:
            s = ssl.create_default_context().wrap_socket(s, server_hostname=self.host)
        self.sock = s
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {self.path} HTTP/1.1\r\nHost: {self.host}:{self.port}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                raise WSError("handshake: connection closed")
            buf += chunk
            if len(buf) > 65536:
                raise WSError("handshake: oversized response")
        head, rest = buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in lines[0] + " ":
            raise WSError(f"handshake: {lines[0]}")
        expect = base64.b64encode(sha1((key + self.GUID).encode()).digest()).decode()
        hdrs = {}
        for ln in lines[1:]:
            k, _, v = ln.partition(":")
            hdrs[k.strip().lower()] = v.strip()
        if hdrs.get("sec-websocket-accept") != expect:
            raise WSError("handshake: bad Sec-WebSocket-Accept")
        self._rx = rest

    # ----- low-level -----
    def _readex(self, n):
        while len(self._rx) < n:
            chunk = self.sock.recv(max(4096, n - len(self._rx)))
            if not chunk:
                raise WSError("closed by peer")
            self._rx += chunk
        out, self._rx = self._rx[:n], self._rx[n:]
        return out

    def _send_lock_held(self, opcode, payload=b"", fin=True):
        b0 = (self.FIN if fin else 0) | opcode
        n = len(payload)
        hdr = bytes([b0])
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack("!Q", n)
        mask = os.urandom(4)
        pad = (4 - len(payload) % 4) % 4
        padded = payload + b"\x00" * pad
        key = mask * (len(padded) // 4)
        masked = (int.from_bytes(padded, "big")
                  ^ int.from_bytes(key, "big")).to_bytes(len(padded), "big")[:len(payload)]
        self.sock.sendall(hdr + mask + masked)

    def send(self, payload, binary=False):
        if isinstance(payload, str):
            payload = payload.encode()
            op = self.TEXT
        else:
            op = self.BIN
        with self._send_lock:
            self._send_lock_held(op, payload)

    def ping(self, data=b""):
        with self._send_lock:
            self._send_lock_held(self.PING, data)

    def close(self, code=1000):
        try:
            with self._send_lock:
                self._send_lock_held(self.CLOSE, struct.pack("!H", code))
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass

    # ----- frame reader -----
    def _frame(self):
        b0, b1 = self._readex(2)
        fin, op = b0 & self.FIN, b0 & 0x0F
        if b1 & 0x80:
            raise WSError("masked frame from server")
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._readex(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._readex(8))[0]
        if n > 32 * 1024 * 1024:
            raise WSError("frame too large")
        data = self._readex(n) if n else b""
        return fin, op, data

    def _handle(self, fin, op, data):
        """One decoded frame -> result tuple or None (= keep reading)."""
        if op == self.PING:
            with self._send_lock:
                self._send_lock_held(self.PONG, data)
            return None
        if op == self.PONG:
            return ("pong", None)
        if op == self.CLOSE:
            self.close()
            return ("close", None)
        if op == self.CONT:
            self._frag += data
            if fin:
                out, op2 = bytes(self._frag), self._frag_op
                self._frag = bytearray()
                self._frag_op = None
                return ("binary" if op2 == self.BIN else "text", out)
            return None
        if fin:
            return ("text" if op == self.TEXT else "binary", data)
        self._frag = bytearray(data)
        self._frag_op = op
        return None

    def recv(self, timeout=0.3):
        """Return ('text'|'binary', bytes) | ('pong'|'close', None) |
        ('timeout', None). Pings answered transparently. Buffered bytes are
        parsed BEFORE select(): the kernel can hand us two frames in one read
        (e.g. welcome+cmd) and the second must not sit in our buffer while we
        wait on a socket that has nothing more to say."""
        deadline = time.monotonic() + timeout
        try:
            while True:
                if self._rx or self._frag:
                    # data already in hand: parse without blocking the socket
                    self.sock.setblocking(False)
                    try:
                        fin, op, data = self._frame()
                    except BlockingIOError:
                        self.sock.setblocking(True)
                        # frame half-arrived: fall through to select/read
                        fin = None
                    else:
                        self.sock.setblocking(True)
                        res = self._handle(fin, op, data)
                        if res:
                            return res
                        continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return ("timeout", None)
                try:
                    if not select.select([self.sock], [], [], remaining)[0]:
                        return ("timeout", None)
                except (OSError, ValueError):
                    return ("close", None)
                self.sock.setblocking(True)
                fin, op, data = self._frame()
                res = self._handle(fin, op, data)
                if res:
                    return res
        except (WSError, OSError, socket.timeout) as e:
            log.debug("ws recv ended: %s", e)
            return ("close", None)


# --------------------------------------------------------------------- HTTP
class LinkError(Exception):
    pass


class Client:
    AUTH = None  # set in __init__

    def __init__(self):
        if not ROBOT_ID:
            sys.exit("WEBCTL_ROBOT_ID is required")
        self.opener = build_opener(ProxyHandler({}))  # LAN must ignore proxies
        cred = base64.b64encode(f"{ROBOT_ID}:{ROBOT_TOKEN}".encode()).decode()
        self.auth_hdr = {"Authorization": f"Basic {cred}"}
        self.lock = threading.Lock()
        self.held = (0.0, 0.0)
        self.last_ok = 0.0
        self.contacted_once = False
        self.tripped = False
        self.trips = 0
        self.publishes = 0
        self.state = "starting"
        self.sink = None               # built in run(): logs precede any slow init
        self.ws = None                 # StdWs when connected (net thread owns)
        self.ws_is_live = False
        self._frame = None             # latest-only pending JPEG
        self._frame_lock = threading.Lock()
        self._scan = None              # latest-only pending scan dict
        self._scan_lock = threading.Lock()
        self._scan_last_tx = 0.0       # monotonic of last scan upload (pacing)
        self.cam_ts = 0.0              # monotonic stamps for telemetry OK flags
        self.scan_ts = 0.0
        self._topics_cache = (0.0, {})  # (checked_at, {"image": [...], "scan": [...]})
        self.navd = None               # NavDaemon when WEBCTL_NAV=1

    # ---------- velocity ----------
    def apply(self, vx, wz, estop=False):
        with self.lock:
            self.held = (float(vx), float(wz))
            self.last_ok = time.monotonic()
            self.contacted_once = True
            self.tripped = False
            self.state = "estop" if estop else "driven"
        if estop and self.navd:
            # latched e-stop must ALSO kill autonomy: a Nav2 goal that is
            # merely publish-blocked would resume the moment the gate opens.
            self.navd.halt("estop")

    def touch(self):                    # server alive, but no new cmd payload
        with self.lock:
            self.last_ok = time.monotonic()
            self.contacted_once = True
            self.tripped = False

    def effective(self):
        with self.lock:
            vx, wz = self.held
            if not self.contacted_once:
                return 0.0, 0.0
            ok_age = time.monotonic() - self.last_ok
        if ok_age > WATCHDOG:
            if not self.tripped:
                self.trips += 1
                self.tripped = True
                log.warning("watchdog TRIPPED (no contact %.2fs) -> zero", ok_age)
                if self.navd:
                    # link down means robot stopped — autonomy included
                    self.navd.halt("watchdog")
            self.state = "watchdog"
            return 0.0, 0.0
        return vx, wz

    # ---------- http ----------
    def _http(self, method, path, body=None, headers=None, timeout=6.0):
        req = urllib.request.Request(SERVER + path, data=body, method=method,
                                     headers={**self.auth_hdr, **(headers or {})})
        try:
            with self.opener.open(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except Exception as e:
            raise LinkError(str(e)) from e

    # ---------- networking (single thread owns the ws) ----------
    def net_loop(self):
        want_ws = TRANSPORT in ("auto", "ws")
        last_try = 0.0
        fails = 0
        last_ping = 0.0
        seq = None
        while running:
            now = time.monotonic()
            if self.ws is None and want_ws and (now - last_try) >= min(5.0, 0.5 * (2 ** min(fails, 4))):
                last_try = now
                url = (SERVER.replace("http://", "ws://").replace("https://", "wss://")
                       + f"/ws/robotctl/{ROBOT_ID}?token={urllib.parse.quote(ROBOT_TOKEN, safe='')}")
                try:
                    ws = StdWs(url, timeout=5.0)
                    ws.connect()
                    ws.send(json.dumps({"type": "hello", "id": ROBOT_ID,
                                        "cam": cam_enabled(), "scan": scan_enabled(),
                                        "sw": VERSION}))
                    self.ws = ws
                    fails = 0
                    self.state = "ws"
                    log.info("websocket up -> %s", url.split("?")[0])
                except Exception as e:
                    fails += 1
                    self.state = f"ws down ({e})"
                    if fails in (1, 2, 5) or fails % 10 == 0:
                        log.warning("ws connect failed (%d): %s%s", fails, e,
                                    " — using HTTP fallback" if TRANSPORT == "auto" else "")
            if self.ws is not None:
                # unconditional keepalive: busy cmd bursts starve a
                # timeout-only ping (v3.0.2)
                if WS_PING_S and now - last_ping > WS_PING_S:
                    last_ping = now
                    try:
                        self.ws.ping(b"p")
                    except Exception:
                        pass
                op, payload = self.ws.recv(0.3)
                if op == "timeout":
                    continue
                if op == "close":
                    log.warning("websocket dropped -> %s",
                                "HTTP fallback" if TRANSPORT == "auto" else "reconnecting")
                    self.ws = None
                    continue
                if op == "pong":
                    self.touch()
                    continue
                if op == "text":
                    try:
                        m = json.loads(payload)
                    except ValueError:
                        continue
                    kind = m.get("type")
                    if kind in ("cmd", "welcome"):
                        seq = m.get("seq", seq)
                        self.apply(m.get("vx", 0.0), m.get("wz", 0.0),
                                   bool(m.get("estop")))
                        navm = m.get("nav")
                        if navm and self.navd:
                            self.navd.handle(navm)
                    elif kind == "takeover":
                        log.warning("another robotctl connection took over; idling 5s")
                        try:
                            self.ws.close()
                        except Exception:
                            pass
                        self.ws = None
                        time.sleep(5.0)
                continue
            if TRANSPORT == "ws":
                time.sleep(0.2)
                continue
            # -------- HTTP long-poll fallback --------
            params = f"wait={POLL_WAIT}" + (f"&seq={seq}" if seq is not None else "")
            try:
                code, body = self._http("GET", f"/robotctl/{ROBOT_ID}/cmd?{params}",
                                        timeout=POLL_WAIT + 4.0)
                if code == 200:
                    d = json.loads(body)
                    seq = d["seq"]
                    self.apply(d["vx"], d["wz"], bool(d.get("estop")))
                    navm = d.get("nav")
                    if navm and self.navd:
                        self.navd.handle(navm)
                elif code in (401, 403, 404, 503):
                    self.state = f"http {code}"
                    log.error("cmd poll rejected (%d): %s", code, body[:200])
                    time.sleep(1.0)
                else:
                    raise LinkError(f"http {code}")
            except LinkError as e:
                self.state = f"http link error"
                log.warning("cmd poll failed: %s", e)
                time.sleep(0.3)

    # ---------- velocity out ----------
    def publish_loop(self):
        dt = 1.0 / PUBLISH_HZ
        was_driving = False
        while running:
            vx, wz = self.effective()
            if self.navd and self.navd.driving:
                # Nav2 owns /cmd_vel while a route runs — publishing our
                # (zero) teleop here at 10 Hz would FIGHT its controller.
                was_driving = True
                time.sleep(dt)
                continue
            if was_driving:
                # autonomy just released the topic: never hand back a stale
                # held velocity — flush a zero first.
                was_driving = False
                with self.lock:
                    self.held = (0.0, 0.0)
                vx, wz = 0.0, 0.0
            try:
                self.sink.publish(vx, wz, {
                    "publishes": self.publishes,
                    "watchdog_trips": self.trips,
                    "state": self.state,
                    "transport": "ws" if self.ws else "http",
                })
            except Exception as e:
                log.error("velocity publish failed: %s", e)
            self.publishes += 1
            time.sleep(dt)
        for _ in range(5):
            try:
                self.sink.publish(0.0, 0.0, {"publishes": self.publishes,
                                             "watchdog_trips": self.trips,
                                             "state": "shutdown"})
            except Exception:
                pass
            time.sleep(0.1)

    # ---------- telemetry ----------
    def battery_v(self):
        if DRY_RUN:
            return DRY_BATTERY
        return getattr(self.sink, "battery_v", None)

    def ros_topics(self):
        """Live ROS graph snapshot (image + laser topics), cached 30 s.
        Remote diagnostics: shows up in fleet status so 'camera is black' can
        be diagnosed from the BROWSER — no SSH round-trip to the robot."""
        if DRY_RUN:
            return {}
        checked, cached = self._topics_cache
        if time.monotonic() - checked < 30.0:
            return cached
        out = {"image": [], "scan": []}
        try:
            node = getattr(self.sink, "node", None)
            if node is not None:
                for name, types_ in node.get_topic_names_and_types():
                    for t in types_:
                        if t.endswith("msg/Image") or t.endswith("msg/CompressedImage"):
                            out["image"].append(name)
                        elif t.endswith("msg/LaserScan"):
                            out["scan"].append(name)
                out["image"] = sorted(out["image"])[:12]
                out["scan"] = sorted(out["scan"])[:12]
        except Exception as e:
            log.debug("topic probe failed: %s", e)
        self._topics_cache = (time.monotonic(), out)
        return out

    def telemetry_loop(self):
        while running:
            vx, wz = self.effective()
            now = time.monotonic()
            body = {"type": "telemetry", "battery_v": self.battery_v(),
                    "applied_vx": vx, "applied_wz": wz,
                    "watchdog_trips": self.trips, "transport":
                        "ws" if self.ws else "http",
                    "cam_ok": self.cam_ts and now - self.cam_ts < 5.0,
                    "scan_ok": self.scan_ts and now - self.scan_ts < 5.0,
                    "ros_topics": self.ros_topics()}
            if self.navd:
                body["nav"] = self.navd.snapshot()
            try:
                if self.ws:
                    self.ws.send(json.dumps(body))
                else:
                    self._http("POST", f"/robotctl/{ROBOT_ID}/telemetry",
                               body=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
            except Exception as e:
                log.debug("telemetry failed: %s", e)
            time.sleep(1.0)

    # ---------- camera ----------
    def post_frame(self, jpeg):
        """Camera thread hands the newest JPEG here; anything still waiting
        to go out is replaced (latest-wins)."""
        self.cam_ts = time.monotonic()
        with self._frame_lock:
            self._frame = jpeg

    def post_scan(self, scan):
        self.scan_ts = time.monotonic()
        with self._scan_lock:
            self._scan = scan

    def frame_loop(self):
        """Dedicated uploader: at most ONE frame in flight, stale frames
        dropped. A blocking ws.send on congested WiFi used to hold the
        shared socket ~1s and starve ping/pong -> watchdog false trip.
        Scans ride the same thread (small JSON, paced to SCAN_HZ) so only
        this thread ever writes bulk-ish payloads to the socket."""
        scan_gap = 1.0 / max(0.5, SCAN_HZ)
        while running:
            with self._frame_lock:
                jpeg, self._frame = self._frame, None
            with self._scan_lock:
                scan, self._scan = self._scan, None
            if scan and time.monotonic() - self._scan_last_tx < scan_gap:
                with self._scan_lock:            # too young: keep as pending
                    if self._scan is None:
                        self._scan = scan
                    scan = None
            if not jpeg and not scan:
                time.sleep(0.02)
                continue
            try:
                ws = self.ws
                if jpeg:
                    if ws is not None:
                        ws.send(jpeg, binary=True)
                    else:
                        self._http("POST", f"/robotctl/{ROBOT_ID}/frame", body=jpeg,
                                   headers={"Content-Type": "image/jpeg"},
                                   timeout=4.0)
                if scan:
                    self._scan_last_tx = time.monotonic()
                    payload = json.dumps(scan)
                    if ws is not None:
                        ws.send(payload)         # TEXT frame: type=scan
                    else:
                        self._http("POST", f"/robotctl/{ROBOT_ID}/scan",
                                   body=payload.encode(),
                                   headers={"Content-Type": "application/json"},
                                   timeout=4.0)
            except Exception:
                pass                      # dropped frames are fine; newest next
            # tiny yield so a fast camera can't spin the uploader ahead of
            # everything else on the socket
            time.sleep(0.01)

    def cam_loop(self, source=None):
        if source is None:                 # lazy: resolved off the main thread
            log.info("camera: resolving source (mode=%s)...", CAM_MODE)
            source = make_cam()
            if source is None:
                return
        period = 1.0 / max(1.0, CAM_FPS)
        next_t = time.monotonic()
        while running:
            try:
                jpeg = source.read()
                if jpeg:
                    self.post_frame(jpeg)  # uploader thread does the sending
            except Exception as e:
                log.warning("camera source error: %s (continuing, driving is unaffected)", e)
                time.sleep(1.0)
            next_t += period
            delay = next_t - time.monotonic()
            if delay < -period:
                next_t = time.monotonic()
            elif delay > 0:
                time.sleep(delay)

    def scan_loop(self, source=None):
        """LiDAR reader thread: resolve source (synthetic when dry-running),
        pack newest scan, hand to the uploader. Never blocks driving."""
        if source is None:                     # lazy, off the main thread
            log.info("lidar: resolving source (mode=%s)...", SCAN_MODE)
            source = make_scan()
            if source is None:
                return
        while running:
            try:
                scan = source.read()
                if scan:
                    self.post_scan(scan)       # uploader thread paces the send
            except Exception as e:
                log.warning("lidar source error: %s (driving unaffected)", e)
                time.sleep(1.0)

    # ---------- sinks ----------
    def _make_sink(self):
        if not DRY_RUN:
            return RosSink()
        if not SINK:
            log.warning("WEBCTL_SINK not set; dry-run velocities go nowhere")

            class _Null(Sink):
                def __init__(self):
                    super().__init__(os.devnull)
            return _Null()
        return Sink(SINK)

    def run(self):
        log.info("robotctl %s client '%s' -> %s (transport=%s watchdog=%.2fs cam=%s)",
                 VERSION, ROBOT_ID, SERVER, TRANSPORT, WATCHDOG, CAM_MODE)
        log.info("initialising velocity sink (dry_run=%s; first rclpy start "
                 "can take a few seconds)...", DRY_RUN)
        self.sink = self._make_sink()
        if NAV_ON:
            # explicit same-dir load: works whether client.py is run as a
            # script, imported by the test harness, or copied to the robot
            try:
                import importlib.util
                d = os.path.dirname(os.path.abspath(__file__))
                spec = importlib.util.spec_from_file_location(
                    "webctl_navd", os.path.join(d, "navd.py"))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                self.navd = mod.NavDaemon(self.sink, post=self._http,
                                          dry=DRY_RUN, robot_id=ROBOT_ID)
                log.info("autonomous nav helper ACTIVE (WEBCTL_NAV=1)")
            except Exception as e:
                log.error("nav helper disabled (import/init failed): %s", e)
                self.navd = None
        threads = [threading.Thread(target=self.net_loop, daemon=True, name="net"),
                   threading.Thread(target=self.publish_loop, daemon=True, name="publish"),
                   threading.Thread(target=self.telemetry_loop, daemon=True, name="telemetry")]
        if CAM_MODE != "off":
            # camera resolves INSIDE its thread — a slow/hanging device probe
            # must never delay the control link or this log stream.
            threads.append(threading.Thread(target=self.cam_loop, daemon=True, name="cam"))
        if CAM_MODE != "off" or scan_enabled():
            # the uploader carries JPEGs AND scans — start it for either
            threads.append(threading.Thread(target=self.frame_loop, daemon=True,
                                            name="frame-uploader"))
        if scan_enabled():
            threads.append(threading.Thread(target=self.scan_loop, daemon=True,
                                            name="lidar"))
        for t in threads:
            t.start()
        while running:
            time.sleep(0.2)
        if self.navd:
            self.navd.halt("shutdown")   # cancel goal + stop nav stack
        log.info("robotctl client shutting down")


# ------------------------------------------------------------ sinks (ROS2)
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
        if self.path == os.devnull:
            return
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path)
                                   or tempfile.gettempdir())
        with os.fdopen(fd, "w") as fh:
            json.dump({"vx": vx, "wz": wz, **state}, fh)
        os.replace(tmp, self.path)


class RosSink:
    """Real robot: rclpy node with /cmd_vel publisher + battery subscribers.
    rclpy ships in the WHEELTEC ROS2 image — nothing is installed for it."""

    def __init__(self):
        log.info("rclpy: initialising node 'webctl_robot_client' "
                 "(DDS discovery on first boot can take a few seconds)...")
        import rclpy
        from geometry_msgs.msg import Twist
        from std_msgs.msg import Float32
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.qos import QoSProfile

        self._Twist = Twist
        rclpy.init()
        self.node = rclpy.create_node("webctl_robot_client")
        self.pub = self.node.create_publisher(Twist, CMD_VEL_TOPIC, 10)
        self.battery_v = None
        # depth-1, reliable+volatile defaults — matches the wheeltec drivers;
        # deliberately NO policy enums here (member names drift across ROS2
        # distros, and RELIABLE was never a *durability* policy anyway)
        qos = QoSProfile(depth=1)
        for topic in BATTERY_TOPICS:
            try:
                self.node.create_subscription(
                    Float32, topic, lambda m, t=topic: self._batt(m, t), qos)
            except Exception as e:
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


# --------------------------------------------------------- camera sources
def cam_enabled():
    return CAM_MODE != "off"


def scan_enabled():
    return SCAN_MODE != "off"


def discover_topics(node, type_suffix):
    """Every topic on the live ROS graph whose msg type ends with
    type_suffix (e.g. 'msg/LaserScan'). Empty on any failure — never raises."""
    try:
        return [name for name, types_ in node.get_topic_names_and_types()
                if any(str(t).endswith(type_suffix) for t in types_)]
    except Exception as e:
        log.debug("topic discovery failed: %s", e)
        return []


def pack_scan(msg, topic="", rays=None, max_m=None):
    """Downsample a sensor_msgs/LaserScan to a compact JSON-safe dict for the
    uplink. Spec rules honoured (docs.ros.org LaserScan): angles increase
    counterclockwise from +x; readings outside [range_min, range_max] or
    inf/nan mean 'no return' -> 0.0 (browser renders the gap)."""
    rays = SCAN_RAYS if rays is None else rays
    max_m = SCAN_MAX_M if max_m is None else max_m
    ranges = list(msg.ranges)
    n = len(ranges)
    if n == 0:
        return None
    stride = 1 if rays <= 0 else max(1, (n + rays - 1) // rays)
    rmin, rmax = float(msg.range_min), float(msg.range_max)
    out = []
    for i in range(0, n, stride):
        try:
            v = float(ranges[i])
        except (TypeError, ValueError):
            out.append(0.0)
            continue
        if (v != v) or v <= 0.0 or v == float("inf") or v == float("-inf") \
                or v < rmin or v > min(rmax, max_m):
            out.append(0.0)               # invalid/out-of-window per spec
        else:
            out.append(round(v, 3))
    try:
        frame = str(getattr(getattr(msg, "header", None), "frame_id", "") or "")
    except Exception:
        frame = ""
    return {"type": "scan", "frame": frame[:64], "topic": topic[:64],
            "amin": round(float(msg.angle_min), 4),
            "amax": round(float(msg.angle_max), 4),
            "ainc": round(float(msg.angle_increment) * stride, 5),
            "rmin": round(rmin, 3), "rmax": round(min(rmax, max_m), 3),
            "ranges": out}


class MjpegSource:
    """Consume a LOCAL MJPEG stream (e.g. web_video_server on 127.0.0.1 if
    the image has it) and hand back one JPEG per read(). SOI/EOI scan is
    boundary-agnostic and safe: 0xFF is byte-stuffed in JPEG payloads."""

    def __init__(self, url):
        u = urllib.parse.urlsplit(url)
        self.host, self.port = u.hostname, u.port or 80
        self.path = (u.path or "/") + (("?" + u.query) if u.query else "")
        self.conn = None
        self.buf = b""

    def _connect(self):
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = http.client.HTTPConnection(self.host, self.port, timeout=15)
        self.conn.request("GET", self.path, headers={"Accept": "multipart/x-mixed-replace"})
        r = self.conn.getresponse()
        if r.status != 200:
            raise WSError(f"mjpeg server returned {r.status}")
        # read1: HTTPResponse.read(n) on an unknown-length stream BLOCKS until
        # n bytes arrive — a 10 fps MJPEG trickle would then stall minutes.
        self._read = getattr(r, "read1", r.read)
        self.resp = r

    def read(self):
        for _ in range(3):
            try:
                if self.conn is None:
                    self._connect()
                while True:
                    soi = self.buf.find(b"\xff\xd8")
                    if soi >= 0:
                        eoi = self.buf.find(b"\xff\xd9", soi + 4)
                        if eoi > 0:
                            jpeg = self.buf[soi:eoi + 2]
                            self.buf = self.buf[eoi + 2:]
                            return jpeg
                        self.buf = self.buf[soi:][: 8 << 20]
                    chunk = self._read(65536)
                    if not chunk:
                        raise WSError("mjpeg stream ended")
                    self.buf += chunk
            except Exception as e:
                self.conn = None
                log.warning("mjpeg reconnect: %s", e)
                time.sleep(1.0)
        return None


class Cv2Source:
    """USB/CSI camera via OpenCV — preinstalled on Jetson images (cv2),
    used only if importable. WEBCTL_CAM_DEVICE: index, or a full gstreamer
    pipeline string for CSI cameras (nvarguscamerasrc)."""

    def __init__(self, device):
        import cv2
        self._cv2 = cv2
        if device and not device.isdigit():
            self.cap = cv2.VideoCapture(device, cv2.CAP_GSTREAMER)
        else:
            self.cap = cv2.VideoCapture(int(device or 0))
        if not self.cap.isOpened():
            raise WSError("cv2 cannot open camera "
                          f"{device or 0!r} — try WEBCTL_CAM_DEVICE or a gst pipeline")
        log.info("camera: cv2 device %s opened", device or 0)

    def read(self):
        ok, frame = self.cap.read()
        if not ok or frame is None:
            time.sleep(0.05)
            return None
        h, w = frame.shape[:2]
        if w > CAM_WIDTH:
            frame = self._cv2.resize(frame, (CAM_WIDTH, int(h * CAM_WIDTH / w)))
        ok, buf = self._cv2.imencode(".jpg", frame,
                                     [self._cv2.IMWRITE_JPEG_QUALITY, CAM_JPEG_Q])
        return buf.tobytes() if ok else None


class RosCamSource:
    """Camera straight from the ROS2 graph (the WHEELTEC API): subscribe to
    the sensor_msgs/Image the kit's camera stack publishes and JPEG-encode
    with cv2 (both ship on the robot image)."""

    def __init__(self, topics):
        import rclpy
        from rclpy.qos import qos_profile_sensor_data
        from sensor_msgs.msg import Image
        self._Image = Image
        self._qos = qos_profile_sensor_data
        self.node = rclpy.create_node("webctl_cam") if rclpy.ok() else None
        if self.node is None:
            raise WSError("rclpy not initialised")
        self.last = None
        self.warned_enc = set()
        self.subscribed = set()
        self.rx_total = 0
        self.next_discover = time.monotonic() + SCAN_DISCOVER_S
        self.discovered_once = False
        got = False
        for t in topics:
            got = self.subscribe(t) or got
        if not got:
            raise WSError("no subscribable camera topic")
        # THIS was the silent-frame-killer: ROS callbacks only run while some
        # executor spins this node — sink has its own; the cam node needs one.
        from rclpy.executors import SingleThreadedExecutor
        ex = SingleThreadedExecutor()
        ex.add_node(self.node)
        threading.Thread(target=ex.spin, daemon=True, name="rclpy-cam-spin").start()
        import numpy
        self._np = numpy
        import cv2
        self._cv2 = cv2
        log.info("camera: ros subscription(s) %s", sorted(self.subscribed))

    def subscribe(self, topic):
        if topic in self.subscribed:
            return True
        node = self.node
        if node is None:
            return False
        try:
            node.create_subscription(
                self._Image, topic,
                lambda m, tt=topic: self._rx(m, tt),
                self._qos)
            self.subscribed.add(topic)
            return True
        except Exception as e:
            log.warning("camera topic %s: %s", topic, e)
            return False

    def _rx(self, msg, topic):
        self.rx_total += 1
        self.last = (topic, msg)

    def _maybe_discover(self):
        """If candidate names never delivered, hunt the live graph for ANY
        sensor_msgs/msg/Image topic (vendor naming differs; camera node may
        start long after this client). Self-heals without config or restarts."""
        now = time.monotonic()
        if self.rx_total or now < self.next_discover:
            return
        self.next_discover = now + SCAN_DISCOVER_S
        found = [t for t in discover_topics(self.node, "msg/Image")
                 if t not in self.subscribed]
        added = [t for t in found if self.subscribe(t)]
        if added:
            log.info("camera: DISCOVERED live image topic(s) %s — subscribing",
                     ", ".join(sorted(added)))
        elif not self.discovered_once and not self.subscribed:
            self.discovered_once = True
            log.warning("camera: no Image topic on the ROS graph — is the "
                        "camera node running? (ros2 topic list | grep image)")
        elif not found and not self.discovered_once:
            self.discovered_once = True
            log.warning("camera: subscribed %s but zero frames received; "
                        "graph has no other Image topic — check encoding/"
                        "publisher (ros2 topic info -v <topic>)",
                        sorted(self.subscribed))

    # encoding -> (bytes per pixel, OpenCV conversion to BGR; None = already BGR)
    _PACKED = {"rgb8": (3, "COLOR_RGB2BGR"), "bgr8": (3, None),
               "rgba8": (4, "COLOR_RGBA2BGR"), "bgra8": (4, "COLOR_BGRA2BGR"),
               "mono8": (1, "COLOR_GRAY2BGR"),
               # packed 4:2:2 — usb_cam's raw default (the old black-video bug)
               "yuyv422": (2, "COLOR_YUV2BGR_YUYV"),
               "uyvy": (2, "COLOR_YUV2BGR_UYVY"),
               "yuv422": (2, "COLOR_YUV2BGR_Y422")}
    _PLANAR = {"nv12": "COLOR_YUV2BGR_NV12", "nv21": "COLOR_YUV2BGR_NV21",
               "i420": "COLOR_YUV2BGR_I420", "yuv420": "COLOR_YUV2BGR_I420"}

    def read(self):
        got = self.last
        if not got:
            self._maybe_discover()
            time.sleep(0.05)
            return None
        _, msg = got
        self.last = None
        np = self._np
        enc, h, w = msg.encoding, msg.height, msg.width
        step = int(msg.step or 0)
        try:
            buf = np.frombuffer(msg.data, dtype=np.uint8)
            if enc in self._PACKED:
                ch, conv = self._PACKED[enc]
                need = w * ch
                # respect msg.step (rows can be padded — width*ch slicing
                # miscolours those streams); fall back to tight layout
                if step >= need:
                    img = buf[:h * step].reshape(h, step)[:, :need].reshape(h, w, ch)
                else:
                    img = buf[:h * need].reshape(h, w, ch)
            elif enc in self._PLANAR:
                conv = self._PLANAR[enc]
                if step and step != w:      # padded planar: bail, log it once
                    raise ValueError(f"padded planar step={step}")
                img = buf[:h * w * 3 // 2].reshape(h * 3 // 2, w)
            else:
                if enc not in self.warned_enc:
                    self.warned_enc.add(enc)
                    log.warning("camera: unsupported encoding %r (topic dropped)",
                                enc)
                return None
            if conv:
                img = self._cv2.cvtColor(img, getattr(self._cv2, conv))
        except Exception as e:
            key = enc + ":decode"
            if key not in self.warned_enc:
                self.warned_enc.add(key)
                log.warning("camera decode failed for encoding %r: %s", enc, e)
            return None
        h, w = img.shape[:2]
        if w > CAM_WIDTH:
            img = self._cv2.resize(img, (CAM_WIDTH, int(h * CAM_WIDTH / w)))
        ok, buf = self._cv2.imencode(".jpg", img,
                                     [self._cv2.IMWRITE_JPEG_QUALITY, CAM_JPEG_Q])
        return buf.tobytes() if ok else None


class RosScanSource:
    """LiDAR straight from the ROS2 graph: subscribe to sensor_msgs/LaserScan
    (LSLidar M10P publishes /scan via wheeltec_lidar.launch.py), pack newest."""

    def __init__(self, topics):
        import rclpy
        from rclpy.qos import qos_profile_sensor_data
        from sensor_msgs.msg import LaserScan
        self._LaserScan = LaserScan
        self._qos = qos_profile_sensor_data
        self.node = rclpy.create_node("webctl_scan") if rclpy.ok() else None
        if self.node is None:
            raise WSError("rclpy not initialised")
        self.last = None                 # (topic, msg)
        self.subscribed = set()
        self.rx_total = 0
        self.next_discover = time.monotonic() + SCAN_DISCOVER_S
        self.warned_none = False
        for t in topics:
            self.subscribe(t)
        from rclpy.executors import SingleThreadedExecutor
        ex = SingleThreadedExecutor()
        ex.add_node(self.node)
        threading.Thread(target=ex.spin, daemon=True, name="rclpy-scan-spin").start()
        log.info("lidar: ros subscription(s) %s", sorted(self.subscribed) or "(none yet)")

    def subscribe(self, topic):
        if topic in self.subscribed:
            return True
        node = self.node
        if node is None:
            return False
        try:
            node.create_subscription(
                self._LaserScan, topic,
                lambda m, tt=topic: self._rx(m, tt), self._qos)
            self.subscribed.add(topic)
            return True
        except Exception as e:
            log.debug("lidar topic %s: %s", topic, e)
            return False

    def _rx(self, msg, topic):
        self.rx_total += 1
        self.last = (topic, msg)

    def _maybe_discover(self):
        now = time.monotonic()
        if self.rx_total or now < self.next_discover:
            return
        self.next_discover = now + SCAN_DISCOVER_S
        found = [t for t in discover_topics(self.node, "msg/LaserScan")
                 if t not in self.subscribed]
        added = [t for t in found if self.subscribe(t)]
        if added:
            log.info("lidar: DISCOVERED live LaserScan topic(s) %s — subscribing",
                     ", ".join(sorted(added)))
        elif not self.warned_none:
            self.warned_none = True
            log.warning("lidar: no LaserScan topic on the graph — start it with "
                        "`ros2 launch turn_on_wheeltec_robot "
                        "wheeltec_lidar.launch.py` (LSLidar M10P on ",
                        "/dev/wheeltec_lidar); graph: ros2 topic list")

    def read(self):
        got = self.last
        if not got:
            self._maybe_discover()
            time.sleep(0.05)
            return None
        topic, msg = got
        self.last = None
        try:
            return pack_scan(msg, topic=topic)
        except Exception as e:
            log.warning("lidar pack failed for %s: %s", topic, e)
            return None


class SyntheticScan:
    """Dry-run/test LiDAR: virtual rotating wall ring with a door gap, so the
    whole uplink + UI can be proven without hardware (same pack path)."""

    N = 240
    _2PI = 6.283185307179586

    def __init__(self):
        self.phase = 0.0
        self.next_t = time.monotonic()

    def read(self):
        # hold ~10 Hz updates; uploader still paces to SCAN_HZ
        delay = self.next_t - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.next_t = time.monotonic() + 0.1
        self.phase = (self.phase + 0.03) % self._2PI
        ainc = self._2PI / self.N
        ranges = []
        for i in range(self.N):
            a = -3.141592653589793 + i * ainc
            gap = abs((a - self.phase + 3.141592653589793) % self._2PI
                      - 3.141592653589793)
            ranges.append(0.0 if gap < 0.35 else 4.0 + 0.4 * (i % 7))
        msg = types.SimpleNamespace(
            header=types.SimpleNamespace(frame_id="laser_dry"),
            angle_min=-3.141592653589793, angle_max=3.141592653589793,
            angle_increment=ainc, range_min=0.15, range_max=8.0,
            ranges=ranges)
        return pack_scan(msg, topic="/scan_dry")


def make_scan():
    """Resolve LiDAR source; every failure degrades to no-lidar, never
    no-driving (mirrors make_cam's contract)."""
    try:
        if SCAN_MODE not in ("auto", "ros"):
            raise WSError(f"unknown WEBCTL_SCAN_MODE {SCAN_MODE!r}")
        if DRY_RUN:
            log.info("lidar: dry-run synthetic scan enabled")
            return SyntheticScan()
        topics = ([SCAN_TOPIC] if SCAN_TOPIC else SCAN_CANDIDATES)
        log.info("lidar: trying ROS LaserScan topic(s) %s", ", ".join(topics))
        return RosScanSource(topics)
    except Exception as e:
        log.warning("lidar disabled: %s", e)
        return None


def make_cam():
    """Resolve WEBCTL_CAM_MODE; every failure path degrades to no-camera,
    never to no-driving."""
    global CAM_MODE
    try:
        if CAM_MODE == "mjpeg":
            if not CAM_URL:
                raise WSError("WEBCTL_CAM_URL required for mjpeg mode")
            return MjpegSource(CAM_URL)
        if CAM_MODE == "cv2":
            return Cv2Source(CAM_DEVICE)
        if CAM_MODE == "ros":
            return RosCamSource([CAM_ROS_TOPIC] if CAM_ROS_TOPIC else ROS_CAM_CANDIDATES)
        # auto: ros graph -> cv2 device -> local mjpeg
        if not DRY_RUN:
            try:
                import rclpy  # noqa: F401
                topics = [CAM_ROS_TOPIC] if CAM_ROS_TOPIC else ROS_CAM_CANDIDATES
                log.info("camera: trying ROS image topic(s) %s", ", ".join(topics))
                return RosCamSource(topics)
            except Exception:
                pass
        try:
            log.info("camera: trying cv2 device %s "
                     "(probe can hang on CSI cams — set WEBCTL_CAM_MODE=ros "
                     "or off if it stalls here)", CAM_DEVICE or 0)
            return Cv2Source(CAM_DEVICE)          # no cv2/no device -> passes on
        except Exception:
            pass
        if CAM_URL:
            return MjpegSource(CAM_URL)
        raise WSError("no camera source found (ros/cv2/mjpeg all unavailable)")
    except Exception as e:
        log.warning("camera disabled: %s", e)
        CAM_MODE = "off"
        return None


if __name__ == "__main__":
    if "--version" in sys.argv:
        print(f"robotctl {VERSION} (stdlib-only: ws + http fallback)")
        sys.exit(0)
    logging.basicConfig(
        level=getattr(logging, os.environ.get("WEBCTL_LOG_LEVEL", "INFO").upper(),
                      logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True)
    # stdout banner too: if nothing else ever prints, python never even got
    # here (bad interpreter/env) — distinguishes wrapper vs client silence.
    print(f"robotctl {VERSION} starting "
          f"(python {sys.version.split()[0]}, id={ROBOT_ID or '(unset -> exits)'}"
          f", server={SERVER})", flush=True)
    Client().run()
