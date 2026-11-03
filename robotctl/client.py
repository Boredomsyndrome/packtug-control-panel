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

Other env: WEBCTL_SERVER, WEBCTL_ROBOT_ID/_TOKEN, WEBCTL_TRANSPORT=auto|ws|http,
WEBCTL_WATCHDOG=1.0, WEBCTL_POLL_WAIT=0.4, WEBCTL_CMD_VEL_TOPIC,
WEBCTL_BATTERY_TOPICS, WEBCTL_DRY_RUN=1, WEBCTL_SINK=/tmp/x.json,
WEBCTL_DRY_BATTERY=24.3, WEBCTL_CLIENT_VERSION=2.1-stdlib
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
import urllib.error
import urllib.parse
import urllib.request
from hashlib import sha1
from urllib.request import build_opener, ProxyHandler

log = logging.getLogger("robotctl")
VERSION = os.getenv("WEBCTL_CLIENT_VERSION", "2.1-stdlib")

SERVER = os.getenv("WEBCTL_SERVER", "http://127.0.0.1:8000").rstrip("/")
ROBOT_ID = os.getenv("WEBCTL_ROBOT_ID", "")
ROBOT_TOKEN = os.getenv("WEBCTL_ROBOT_TOKEN", "")
TRANSPORT = os.getenv("WEBCTL_TRANSPORT", "auto").lower()      # auto|ws|http
POLL_WAIT = float(os.getenv("WEBCTL_POLL_WAIT", "0.4"))
WATCHDOG = float(os.getenv("WEBCTL_WATCHDOG", "1.0"))   # noqa: E305
# server traffic only flows when commands CHANGE; the ping keeps "contact"
# alive, so it MUST stay well under WATCHDOG (pong updates last_ok)
WS_PING_S = min(float(os.getenv("WEBCTL_WS_PING_INTERVAL", "0.4")),
                WATCHDOG / 2.0)

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
        self.sink = self._make_sink()
        self.ws = None                 # StdWs when connected (net thread owns)
        self.ws_is_live = False

    # ---------- velocity ----------
    def apply(self, vx, wz, estop=False):
        with self.lock:
            self.held = (float(vx), float(wz))
            self.last_ok = time.monotonic()
            self.contacted_once = True
            self.tripped = False
            self.state = "estop" if estop else "driven"

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
                                        "cam": cam_enabled(), "sw": VERSION}))
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
                op, payload = self.ws.recv(0.3)
                if op == "timeout":
                    if WS_PING_S and time.monotonic() - last_ping > WS_PING_S:
                        last_ping = time.monotonic()
                        try:
                            self.ws.ping(b"p")
                        except Exception:
                            pass
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
        while running:
            vx, wz = self.effective()
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

    def telemetry_loop(self):
        while running:
            vx, wz = self.effective()
            body = {"type": "telemetry", "battery_v": self.battery_v(),
                    "applied_vx": vx, "applied_wz": wz,
                    "watchdog_trips": self.trips, "transport":
                        "ws" if self.ws else "http"}
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
    def cam_loop(self, source):
        period = 1.0 / max(1.0, CAM_FPS)
        next_t = time.monotonic()
        while running:
            try:
                jpeg = source.read()
                if jpeg:
                    ws = self.ws
                    if ws is not None:
                        try:
                            ws.send(jpeg, binary=True)
                        except Exception:
                            pass                      # dropped frames are fine
                    else:
                        try:
                            self._http("POST", f"/robotctl/{ROBOT_ID}/frame",
                                       body=jpeg, headers={"Content-Type": "image/jpeg"},
                                       timeout=4.0)
                        except Exception:
                            pass
            except Exception as e:
                log.warning("camera source error: %s (continuing, driving is unaffected)", e)
                time.sleep(1.0)
            next_t += period
            delay = next_t - time.monotonic()
            if delay < -period:
                next_t = time.monotonic()
            elif delay > 0:
                time.sleep(delay)

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
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        cam_src = make_cam() if CAM_MODE != "off" else None
        log.info("robotctl %s client '%s' -> %s (transport=%s watchdog=%.2fs cam=%s)",
                 VERSION, ROBOT_ID, SERVER, TRANSPORT, WATCHDOG, CAM_MODE)
        threads = [threading.Thread(target=self.net_loop, daemon=True, name="net"),
                   threading.Thread(target=self.publish_loop, daemon=True, name="publish"),
                   threading.Thread(target=self.telemetry_loop, daemon=True, name="telemetry")]
        if cam_src is not None:
            threads.append(threading.Thread(target=self.cam_loop, args=(cam_src,),
                                            daemon=True, name="cam"))
        for t in threads:
            t.start()
        while running:
            time.sleep(0.2)
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
        import rclpy
        from geometry_msgs.msg import Twist
        from std_msgs.msg import Float32
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.qos import QoSDurabilityPolicy, QoSProfile

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
        self.node = rclpy.create_node("webctl_cam") if rclpy.ok() else None
        if self.node is None:
            raise WSError("rclpy not initialised")
        self.last = None
        self.warned_enc = set()
        got = False
        for t in topics:
            try:
                self.node.create_subscription(
                    Image, t, lambda m, tt=t: setattr(self, "last", (tt, m)),
                    qos_profile_sensor_data)
                got = True
            except Exception as e:
                log.warning("camera topic %s: %s", t, e)
        if not got:
            raise WSError("no subscribable camera topic")
        import numpy
        self._np = numpy
        import cv2
        self._cv2 = cv2
        log.info("camera: ros subscription(s) %s", topics)

    def read(self):
        got = self.last
        if not got:
            time.sleep(0.05)
            return None
        _, msg = got
        self.last = None
        enc = msg.encoding
        ch = {"rgb8": 3, "bgr8": 3, "rgba8": 4, "bgra8": 4, "mono8": 1}.get(enc)
        if ch is None:
            if enc not in self.warned_enc:
                self.warned_enc.add(enc)
                log.warning("camera: unsupported encoding %r (topic dropped)", enc)
            return None
        img = self._np.frombuffer(msg.data, dtype=self._np.uint8)
        img = img[:msg.height * msg.width * ch].reshape(msg.height, msg.width, ch) \
            if msg.height * msg.width * ch <= img.size else None
        if img is None:
            return None
        if enc == "rgb8":
            img = self._cv2.cvtColor(img, self._cv2.COLOR_RGB2BGR)
        elif enc == "rgba8":
            img = self._cv2.cvtColor(img, self._cv2.COLOR_RGBA2BGR)
        elif enc == "bgra8":
            img = self._cv2.cvtColor(img, self._cv2.COLOR_BGRA2BGR)
        elif enc == "mono8":
            img = self._cv2.cvtColor(img, self._cv2.COLOR_GRAY2BGR)
        h, w = img.shape[:2]
        if w > CAM_WIDTH:
            img = self._cv2.resize(img, (CAM_WIDTH, int(h * CAM_WIDTH / w)))
        ok, buf = self._cv2.imencode(".jpg", img,
                                     [self._cv2.IMWRITE_JPEG_QUALITY, CAM_JPEG_Q])
        return buf.tobytes() if ok else None


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
                return RosCamSource(topics)
            except Exception:
                pass
        try:
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
    Client().run()
