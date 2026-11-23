#!/usr/bin/env python3
"""Robotless regression tests for the rclpy code paths of robot/client.py.

The robot is the only place rclpy exists, so the two bugs this suite gates
were shipped-to-hardware classes before:

1. sink QoS used QoSDurabilityPolicy.RELIABLE -> AttributeError on the robot
   (RELIABLE is a *reliability* member; enum member names also drift across
   distros — client must not name durability/reliability enums at all).
2. RosCamSource subscriptions are useless unless an executor SPINS the node
   (frames silently never arrive) — and padded rows need msg.step.

Trick: inject fake rclpy/message/cv2 modules into sys.modules, import
client.py, and drive the real code paths. numpy is real (hard requirement
of the decode path on the robot too); if absent, decode test SKIPs.

Run: python3 tools/test_ros_shims.py   (exit 0 = all good)
"""
import sys
import threading
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLIENT = HERE.parent / "robot" / "client.py"

# ---------------------------------------------------------------- fake rclpy
SPINS = []            # (executor, started_event) for every executor built
NODES = []            # every FakeNode ever created


class QoSProfile:
    last_kwargs = None

    def __init__(self, **kw):
        QoSProfile.last_kwargs = kw


def _mod(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


class FakeNode:
    def __init__(self, name):
        self.name = name
        self.subs = []          # (topic, callback)
        self.graph = []         # [(topic, [types])] for get_topic_names_and_types
        NODES.append(self)

    def create_publisher(self, msgtype, topic, qos):
        pub = types.SimpleNamespace(topic=topic, sent=[])
        pub.publish = lambda m, p=pub: p.sent.append(m)
        return pub

    def create_subscription(self, msgtype, topic, cb, qos):
        self.subs.append((topic, cb))
        return object()

    def get_topic_names_and_types(self):
        return list(self.graph)


class SingleThreadedExecutor:
    def __init__(self):
        self.nodes = []
        self.started = threading.Event()
        SPINS.append(self)

    def add_node(self, n):
        self.nodes.append(n)

    def spin(self):
        self.started.set()
        threading.Event().wait()          # blocks forever, like the real one


class QoSProfileFactory:                    # rclpy.qos.QoSProfile passthrough
    pass


rclpy = _mod("rclpy", init=lambda *a, **k: None, ok=lambda: True,
             create_node=lambda name, **k: FakeNode(name))
_mod("rclpy.qos", QoSProfile=QoSProfile,
     qos_profile_sensor_data=object())
_mod("rclpy.executors", SingleThreadedExecutor=SingleThreadedExecutor)
_mod("rclpy.utilities", get_default_context=lambda: object())
for pkg in ("geometry_msgs", "std_msgs", "sensor_msgs"):
    _mod(pkg)
class _Twist:
    def __init__(self):
        self.linear = types.SimpleNamespace(x=0.0, y=0.0, z=0.0)
        self.angular = types.SimpleNamespace(x=0.0, y=0.0, z=0.0)


_mod("geometry_msgs.msg", Twist=_Twist)
_mod("std_msgs.msg", Float32=type("Float32", (), {}))
_mod("sensor_msgs.msg", Image=type("Image", (), {}),
     LaserScan=type("LaserScan", (), {}))
_mod("nav_msgs")
_mod("nav_msgs.msg",
     Odometry=type("Odometry", (), {}),
     OccupancyGrid=type("OccupancyGrid", (), {}))

# fake cv2: pass-through geometry, imencode hands back a marker payload
cv2 = _mod("cv2", COLOR_RGB2BGR=0, COLOR_RGBA2BGR=1, COLOR_BGRA2BGR=2,
           COLOR_GRAY2BGR=3, IMWRITE_JPEG_QUALITY=4,
           COLOR_YUV2BGR_YUYV=10, COLOR_YUV2BGR_UYVY=11, COLOR_YUV2BGR_Y422=12,
           COLOR_YUV2BGR_NV12=13, COLOR_YUV2BGR_NV21=14, COLOR_YUV2BGR_I420=15,
           cvtColor=lambda img, code: img,
           resize=lambda img, size: img,
           imencode=lambda ext, img, params=None:
               (True, types.SimpleNamespace(tobytes=lambda: b"\xff\xd8JPEG\xff\xd9")))

try:
    import numpy  # noqa: F401  (real one; decode math must be exercised)
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

import importlib.util                                    # noqa: E402
spec = importlib.util.spec_from_file_location("robotctl_client", CLIENT)
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)                           # module import only

failures = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + extra if extra else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 1. RosSink
sink = client.RosSink()
check("sink QoSProfile built with depth=1 and NO durability/reliability enums",
      QoSProfile.last_kwargs == {"depth": 1}, repr(QoSProfile.last_kwargs))
src = CLIENT.read_text()
check("client.py names no durability/reliability enum members",
      "QoSDurabilityPolicy" not in src and "QoSReliabilityPolicy" not in src)
check("sink executor spins (battery callbacks will fire)",
      SPINS and SPINS[0].started.wait(2))
sink.publish(0.5, 0.1, "remote")
check("sink publishes Twist on /cmd_vel", getattr(sink.pub, "topic", "") == "/cmd_vel"
      and len(sink.pub.sent) == 1 and abs(sink.pub.sent[0].linear.x - 0.5) < 1e-9)

# ------------------------------------------------------------ 2. RosCamSource
before_nodes = len(NODES)
cam = client.RosCamSource(["/camera/image_raw"])
check("cam node created and SPUN (executor added + spinning)",
      len(NODES) == before_nodes + 1
      and len(SPINS) >= 2 and SPINS[-1].started.wait(2)
      and SPINS[-1].nodes == [NODES[-1]])

if HAVE_NUMPY:
    import numpy as np
    h, w, ch = 2, 4, 3
    step = w * ch + 2                       # padded rows — old code garbled/None
    data = bytearray(h * step)
    for r in range(h):
        for c in range(w):
            data[r * step + c * ch + 2] = 255       # red channel marker
    msg = types.SimpleNamespace(encoding="bgr8", height=h, width=w, step=step,
                                data=bytes(data))
    topic_cb = NODES[-1].subs[0][1]
    topic_cb(msg)                            # fire like rclpy would
    jpeg = cam.read()
    check("padded Image msg decodes to a JPEG frame via subscription callback",
          isinstance(jpeg, bytes) and jpeg.startswith(b"\xff\xd8")
          and jpeg.endswith(b"\xff\xd9"), f"({len(jpeg) if jpeg else 0} bytes)")

    # packed YUYV = usb_cam's raw default; used to be dropped as unsupported
    ystep = w * 2
    ymsg = types.SimpleNamespace(encoding="yuyv422", height=h, width=w,
                                 step=ystep, data=bytes(bytearray(h * ystep)))
    topic_cb(ymsg)
    jy = cam.read()
    check("yuyv422 packed msg decodes (usb_cam raw default)",
          isinstance(jy, bytes) and jy.startswith(b"\xff\xd8"))

    # planar NV12
    nmsg = types.SimpleNamespace(encoding="nv12", height=h, width=w, step=w,
                                 data=bytes(bytearray(h * w * 3 // 2)))
    topic_cb(nmsg)
    jn = cam.read()
    check("nv12 planar msg decodes", isinstance(jn, bytes)
          and jn.startswith(b"\xff\xd8"))

    # unknown encoding: warn-and-drop, never raise, never feed a frame
    umsg = types.SimpleNamespace(encoding="bgra16", height=h, width=w,
                                 step=0, data=b"")
    topic_cb(umsg)
    check("unknown encoding returns None without raising", cam.read() is None)
else:
    print("SKIP  decode test (numpy not installed)")

# ------------------------------------------------- 3. keepalive + frame drop
check("watchdog margin sane: ping interval < watchdog/2",
      0 < client.WS_PING_S * 2 < client.WATCHDOG,
      f"(ping {client.WS_PING_S}s, watchdog {client.WATCHDOG}s)")

c = client.Client.__new__(client.Client)          # skip __init__ (no net)
c._frame, c._frame_lock = None, threading.Lock()
c.post_frame(b"old-frame")
c.post_frame(b"new-frame")
with c._frame_lock:
    pending, c._frame = c._frame, None
check("frame uploader is latest-wins (stale frames dropped, not queued)",
      pending == b"new-frame", repr(pending))

# ------------------------------------------------- 4. lidar: pack_scan spec
def _mk_scan(ranges, amin=-3.14, amax=3.14, ainc=0.02, rmin=0.15, rmax=30.0,
             header=True):
    m = types.SimpleNamespace(angle_min=amin, angle_max=amax,
                              angle_increment=ainc, range_min=rmin,
                              range_max=rmax, ranges=ranges)
    if header:
        m.header = types.SimpleNamespace(frame_id="laser")
    return m


packed = client.pack_scan(_mk_scan([5.0] * 360), topic="/scan")
check("pack_scan: 360 rays downsampled to <=180 with ainc scaled by stride",
      packed and len(packed["ranges"]) == 180 and packed["ranges"][0] == 5.0
      and abs(packed["ainc"] - 0.04) < 1e-9 and packed["frame"] == "laser",
      f"n={len(packed['ranges']) if packed else 0}")

mixed = client.pack_scan(_mk_scan(
    [0.0, -1.0, float("inf"), float("nan"), 50.0, 0.1, 5.123456, 0.15]))
check("pack_scan: inf/nan/neg/over-12m/sub-rmin -> 0 (no return), "
      "valid rounded to mm",
      mixed["ranges"] == [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 5.123, 0.15],
      str(mixed["ranges"]))
check("pack_scan: empty ranges -> None",
      client.pack_scan(_mk_scan([])) is None)
check("pack_scan: missing header tolerated",
      client.pack_scan(_mk_scan([1.0], header=False))["frame"] == "")

# ------------------------------------------------- 5. RosScanSource + discovery
before_nodes = len(NODES)
scan_src = client.RosScanSource(["/scan"])
check("scan node subscribes candidates + executor spins",
      len(NODES) == before_nodes + 1
      and scan_src.node.subs and scan_src.node.subs[0][0] == "/scan"
      and SPINS[-1].started.wait(2))
scan_cb = scan_src.node.subs[0][1]
scan_cb(_mk_scan([2.5] * 20), "/scan")     # fire like rclpy
packed = scan_src.read()
check("received LaserScan -> packed dict via read()",
      packed and packed["topic"] == "/scan" and packed["ranges"][:2] == [2.5, 2.5])

scan_src.rx_total = 0
scan_src.next_discover = 0.0
scan_src.node.graph = [("/rplidar/scan", ["sensor_msgs/msg/LaserScan"]),
                       ("/some/image", ["sensor_msgs/msg/Image"]),
                       ("/scan_filtered", ["sensor_msgs/msg/LaserScan"])]
scan_src._maybe_discover()
check("scan auto-discovery: live LaserScan topics on graph get subscribed "
      "(non-LaserScan skipped)",
      "/rplidar/scan" in scan_src.subscribed
      and "/some/image" not in scan_src.subscribed)

cam.rx_total = 0
cam.next_discover = 0.0
cam.node.graph = [("/vendor/webrtc/image_raw", ["sensor_msgs/msg/Image"])]
cam._maybe_discover()
check("camera auto-discovery: unknown vendor Image topic self-heals "
      "(no config, no restart)",
      "/vendor/webrtc/image_raw" in cam.subscribed)

# ------------------------------------------------- 6. scan slot latest-wins
c2 = client.Client.__new__(client.Client)
c2._scan, c2._scan_lock = None, threading.Lock()
c2.cam_ts = c2.scan_ts = 0.0
c2.post_scan({"seq": 1})
c2.post_scan({"seq": 2})
with c2._scan_lock:
    pending, c2._scan = c2._scan, None
check("scan uploader is latest-wins too", pending == {"seq": 2}, repr(pending))
check("post_scan stamped scan_ts", c2.scan_ts > 0)

# ------------------------------------------------ 7. navd dry state machine
import time as _time                                          # noqa: E402
_navd_spec = importlib.util.spec_from_file_location(
    "robotctl_navd", HERE.parent / "robot" / "navd.py")
navd = importlib.util.module_from_spec(_navd_spec)
_navd_spec.loader.exec_module(navd)


def _wait_nav(nd, pred, timeout=4.0):
    end = _time.time() + timeout
    while _time.time() < end:
        s = nd.snapshot()
        if pred(s):
            return s
        _time.sleep(0.05)
    return nd.snapshot()


_warm_calls = []
_orig_wn = navd.NavDaemon._warm_node
navd.NavDaemon._warm_node = lambda self: (_warm_calls.append(1), _orig_wn(self))
nd = navd.NavDaemon(sink=None, post=None, dry=True, robot_id="shimbot")
_time.sleep(0.3)
check("dry daemon does NOT warm ros2 (v3.6.2)", not _warm_calls)
_rids = iter(range(1, 200))


def _nav_cmd(action, **kw):
    m = {"rid": next(_rids), "action": action}
    m.update(kw)
    nd.handle(m)


_nav_cmd("map_on")
s = _wait_nav(nd, lambda s: s["mode"] == "mapping")
check("navd dry: map_on -> mode=mapping", s["mode"] == "mapping", s["mode"])

nd.handle({"rid": 1, "action": "nav_off"})          # stale rid replay
_time.sleep(0.3)
check("navd: stale/duplicate rid replay ignored",
      nd.snapshot()["mode"] == "mapping")

_nav_cmd("nav_on", map="room1")
s = _wait_nav(nd, lambda s: s["mode"] == "localizing")
check("navd dry: nav_on -> localizing, map remembered",
      s["mode"] == "localizing" and s["map"] == "room1", str(s["map"]))

_nav_cmd("record", name="wpA", point_rid=999.5)
s = _wait_nav(nd, lambda s: s.get("record"))
check("navd dry: record echoes name+pose+point_rid for the hub handshake",
      bool(s.get("record")) and s["record"]["name"] == "wpA"
      and abs(s["record"]["x"] - 0.5) < 1e-9
      and s["record"]["rid"] == 999.5, str(s.get("record")))

_nav_cmd("route", poses=[[1.0, 2.0, 0.0], [3.0, 4.0, 1.0]])
s = _wait_nav(nd, lambda s: s["driving"], timeout=1.5)
check("navd dry: route -> driving owns /cmd_vel", s["driving"]
      and s["mode"] == "route", str(s["mode"]))
s = _wait_nav(nd, lambda s: not s["driving"], timeout=4.0)
check("navd dry: simulated route releases topic, mode->localizing, count=2",
      not s["driving"] and s["mode"] == "localizing" and s["route_done"] == 2,
      str(s))

_nav_cmd("align", align_pose=[1.0, 2.0, 0.0])
_time.sleep(0.3)
s = nd.snapshot()
check("navd dry: align accepted, still localizing, no error",
      s["mode"] == "localizing" and not s["error"], str(s["error"]))

# v3.4.6 semantics (tests/test_halt_semantics.py matrix): SOFT halts keep
# the stack — only explicit nav_off tears it down.
nd.halt("shim-test")
s = _wait_nav(nd, lambda s: s["driving"] is False
              and "shim-test" in (s["error"] or ""))
check("navd: soft halt -> not driving, stack PRESERVED, reason stamped",
      not s["driving"] and s["mode"] == "localizing"
      and "shim-test" in (s["error"] or ""), str(s["error"]))
nd.halt("nav_off")
s = _wait_nav(nd, lambda s: s["mode"] == "off")
check("navd: hard halt (nav_off) -> mode off",
      s["mode"] == "off", str(s["mode"]))

# ------------------------------------------- 8. teleop gate while navd drives
class PubSink:
    def __init__(self):
        self.sent = []

    def publish(self, vx, wz, info=None):
        self.sent.append((vx, wz))


c3 = client.Client.__new__(client.Client)
c3.lock = threading.Lock()
c3.held = (0.3, 0.2)
c3.contacted_once = True
c3.last_ok = _time.monotonic() + 60.0      # keep the watchdog out of it
c3.tripped, c3.trips, c3.state = False, 0, "ok"
c3.ws = None                     # publish_loop reports transport from this
c3.publishes = 0
c3.navd = types.SimpleNamespace(driving=True, halt=lambda r: None)
c3.sink = PubSink()
client.running = True
th = threading.Thread(target=client.Client.publish_loop, args=(c3,),
                      daemon=True)
th.start()
_time.sleep(0.4)
check("client: teleop publisher SILENT while navd drives /cmd_vel",
      c3.sink.sent == [], str(c3.sink.sent))
c3.navd.driving = False
_time.sleep(0.45)
check("client: autonomy release flushes a zero and drops held velocity",
      c3.sink.sent[:1] == [(0.0, 0.0)] and c3.held == (0.0, 0.0),
      str(c3.sink.sent[:2]))
c3.held = (0.25, 0.0)
_time.sleep(0.3)
check("client: teleop resumes at held velocity after release",
      any(abs(v - 0.25) < 1e-9 for v, _ in c3.sink.sent),
      str(c3.sink.sent[-3:]))
client.running = False
th.join(2)

# ---------------------- 7c. v3.5: map upload reliability, heal, local maps
import base64 as _b64, json as _json, os as _os, tempfile as _ttmp, zlib as _zlib

_maps = _ttmp.mkdtemp(prefix="navd-maps-")
navd.MAPS_DIR = _maps
_pgm = b"P5\n2 2\n255\n" + bytes(range(4))
open(_os.path.join(_maps, "corridor.pgm"), "wb").write(_pgm * 20)
open(_os.path.join(_maps, "corridor.yaml"), "w").write("image: corridor.pgm\n")
s = nd.snapshot()
check("navd snapshot reports robot-local maps",
      "corridor" in s["local_maps"], str(s.get("local_maps")))

_calls = {"n": 0}
_caught = []
def _flaky_post(method, path, body=None, headers=None, timeout=None):
    _calls["n"] += 1
    _caught.append((method, path, body))
    if _calls["n"] == 1:
        return (-1, "timed out")            # simulate the dropped link
    return (200, b'{"ok": true}')
nd3 = navd.NavDaemon(sink=None, post=_flaky_post, dry=False,
                     robot_id="shimbot")
_time.sleep(0.5)
check("real daemon warms ros2 node at startup (v3.6.2)", _warm_calls)
# later cases need deterministic node ownership: keep warm a no-op for them
navd.NavDaemon._warm_node = lambda self: None
ok = nd3._upload_map("corridor")
check("upload retry rides out one fake link failure",
      ok and _calls["n"] == 2, f"ok={ok} calls={_calls['n']}")
_d = _json.loads(_caught[-1][2])
check("map upload body is gzip-encoded and byte-round-trips",
      _d.get("enc") == "gz" and
      _zlib.decompress(_b64.b64decode(_d["pgm_gz_b64"])) == _pgm * 20)
check("successful upload marks map uploaded (heal won't resend)",
      "corridor" in nd3._uploaded)
nd3.handle({"rid": _time.time() + 1, "action": "delete_map",
            "map": "corridor"})
_deadline = _time.monotonic() + 3.0
while _os.path.exists(_os.path.join(_maps, "corridor.pgm")) \
        and _time.monotonic() < _deadline:
    _time.sleep(0.1)
check("delete_map action removes the robot-side map files",
      not _os.path.exists(_os.path.join(_maps, "corridor.pgm")))

# initialpose republish loop: stub node, verify republish-until-amcl_pose
import threading as _th2
class _Pub:
    def __init__(self): self.msgs = []
    def publish(self, m): self.msgs.append(m)
class _Node:
    def __init__(self): self.pub = _Pub(); self.subs = {}
    def create_publisher(self, t, topic, qos): return self.pub
    def create_subscription(self, t, topic, cb, qos):
        self.subs[topic] = cb; return object()
    def get_clock(self):
        import types as _t
        return _t.SimpleNamespace(now=lambda: _t.SimpleNamespace(
            to_msg=lambda: _t.SimpleNamespace()))
# shim already fakes geometry_msgs.msg (with Twist) — just add our msg type
sys.modules["geometry_msgs.msg"].PoseWithCovarianceStamped = (
    lambda: types.SimpleNamespace(
        header=types.SimpleNamespace(),
        pose=types.SimpleNamespace(
            pose=types.SimpleNamespace(position=types.SimpleNamespace(),
                                       orientation=types.SimpleNamespace()),
            covariance=[0.0] * 36)))
nd4 = navd.NavDaemon(sink=None, post=None, dry=False, robot_id="shimbot")
nd4._own_node = _Node()
nd4._republish_initial_pose([1.0, 2.0, 0.5])
_time.sleep(1.5)
check("initialpose republishes while AMCL is silent (nav2 pattern)",
      len(nd4._own_node.pub.msgs) >= 1)
nd4._own_node.subs["/amcl_pose"](None)      # AMCL acknowledges
_time.sleep(2.6)                            # outlast any in-flight publish
_n = len(nd4._own_node.pub.msgs)
_time.sleep(2.6)                            # the loop must NOT keep firing
check("republish stops once amcl_pose seen",
      len(nd4._own_node.pub.msgs) == _n,
      f"{_n} -> {len(nd4._own_node.pub.msgs)}")

# ------------------------------------------------ v3.6: route auto-arm + failover
import tempfile as _tf
import os as _os
import importlib as _il

_mapdir = _tf.mkdtemp()
for _ext in (".pgm", ".yaml"):
    open(_os.path.join(_mapdir, "corridor" + _ext), "w").close()
navd.MAPS_DIR = _mapdir

nd5 = navd.NavDaemon(sink=None, post=None, dry=True, robot_id="shimbot")
nd5.handle({"rid": 10, "action": "route", "poses": [[1, 1, 0]]})
s = _wait_nav(nd5, lambda s: s["mode"] == "route", timeout=6.0)
check("route on an IDLE stack auto-arms Nav2 then drives (v3.6)",
      s["mode"] == "route" and s["map"] == "corridor",
      f"mode={s['mode']} map={s['map']}")

open(_os.path.join(_mapdir, "attic.pgm"), "w").close()
open(_os.path.join(_mapdir, "attic.yaml"), "w").close()
nd6 = navd.NavDaemon(sink=None, post=None, dry=True, robot_id="shimbot")
nd6.handle({"rid": 10, "action": "route", "poses": [[1, 1, 0]]})
_time.sleep(0.6)
s = nd6.snapshot()
check("auto-arm refuses AMBIGUOUS map choice with surfaced error",
      "press" in (s["error"] or "") and s["mode"] == "off", s["error"])

_os.environ["WEBCTL_SERVER"] = "http://lan:8090 http://vpn:8090,"
_spec = importlib.util.spec_from_file_location("client_v36_reload", str(CLIENT))
client36 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(client36)          # fresh load with LIST env set
check("WEBCTL_SERVER list parses (space+comma separated)",
      client36.SERVERS == ["http://lan:8090", "http://vpn:8090"],
      str(client36.SERVERS))
c = client36.Client.__new__(client36.Client)
c.server_i = 0
ok1 = c.base() == "http://lan:8090"
c._rotate("t"); ok2 = c.base() == "http://vpn:8090"
c._rotate("t"); ok3 = c.base() == "http://lan:8090"
check("endpoint rotation round-robins", ok1 and ok2 and ok3)

def _boom(req, timeout=None):
    raise OSError("unreachable")
c2 = client36.Client.__new__(client36.Client)
c2.server_i, c2.auth_hdr = 0, {}
c2.opener = types.SimpleNamespace(open=_boom)
try:
    c2._http("GET", "/x")
    ok4 = False
except client36.LinkError:
    ok4 = c2.server_i == 1
check("_http link failure rotates to next endpoint", ok4)

# ---- v3.6.3 half-open ws detector --------------------------------------
# A VPN that dies without RST leaves the ws socket sendable-but-dead: recv
# only ever returns ("timeout", None), ping "succeeds" into the kernel.
# net_loop must treat RX starvation past WS_RX_DEADLINE as fatal and force
# a reconnect/rotate — otherwise a zombie session outlives every redial.
client36.WS_RX_DEADLINE = 0.5


class _ZombieWs:
    dropped = False

    def ping(self, data=b""):
        pass

    def recv(self, timeout=0.3):
        _time.sleep(timeout)
        return ("timeout", None)

    def close(self):
        self.dropped = True


_zw = _ZombieWs()
cz = client36.Client.__new__(client36.Client)
cz.server_i, cz.ws, cz.state, cz.navd = 0, _zw, "ws", None
cz.publishes = cz.trips = 0
cz.lock = threading.Lock()
cz.held = (0.0, 0.0)
cz.ws_lock = getattr(cz, "ws_lock", threading.Lock())
_rot = []
cz._rotate = lambda r: _rot.append(r)
cz.base = lambda: "http://127.0.0.1:1"
cz.apply = lambda *a, **k: None


def _httpf(method, path, body=None, **kw):
    raise client36.LinkError("stub")


cz._http = _httpf
_running_old = client36.running
client36.running = True
_tnet = threading.Thread(target=cz.net_loop, daemon=True)
_tnet.start()
_time.sleep(1.5)
_ho_ok = (cz.ws is None and _zw.dropped
          and any("starved" in str(r) for r in _rot))
client36.running = False
_tnet.join(3)
client36.running = _running_old
check("half-open ws starved -> forced reconnect (v3.6.3)", _ho_ok,
      f"ws_now={cz.ws} rot0={str(_rot[:1])[:60]}")

# ================= v3.7 single-websocket plane + odom passthrough ===========
# (1) map upload: with the ws UP it rides the ws as a {"type":"map"} frame
#     and resolves on map_ack — HTTP must be touched ZERO times. Only with
#     no ws at all (degraded mode) may it fall to HTTP.
import json as _j37


class _StubWs:
    def __init__(self):
        self.sent = []

    def send(self, s, binary=False):
        self.sent.append(s)


cw = client.Client.__new__(client.Client)
cw.ws = _StubWs()
cw._map_acks = {}


def _no_http(*a, **k):
    raise AssertionError("ws-connected client touched HTTP!")


cw._http = _no_http


def _ack_later():
    _time.sleep(0.15)
    for _rid, slot in list(cw._map_acks.items()):
        slot["ok"] = True
        slot["ev"].set()


threading.Thread(target=_ack_later, daemon=True).start()
_code37, _ = cw._server_post("POST", "/robotctl/bot/map",
                             body=b'{"name":"m1","enc":"gz"}', timeout=3.0)
check("v3.7: ws-up map upload rides the ws (type=map frame) with zero HTTP",
      _code37 == 200 and cw.ws.sent
      and _j37.loads(cw.ws.sent[0]).get("type") == "map",
      f"code={_code37} sent={len(cw.ws.sent)}")
cw2 = client.Client.__new__(client.Client)
cw2.ws = None
cw2._map_acks = {}
_hits37 = []
cw2._http = lambda m, p, **k: (_hits37.append(p), (200, b"{}"))[1]
_code37b, _ = cw2._server_post("POST", "/robotctl/bot/map", body=b"{}",
                               timeout=1.0)
check("v3.7: HTTP map path only when there is NO ws at all (degraded)",
      _code37b == 200 and _hits37 == ["/robotctl/bot/map"], str(_hits37))

# (2) /odom telemetry passthrough: VALUES only (no TF broadcast — frozen).
# msg.pose is PoseWithCovariance (msg.pose.pose = Pose) — like nav_msgs.
_odcb = next(cb for _t, cb in sink.node.subs if _t == "/odom")
_odcb(types.SimpleNamespace(pose=types.SimpleNamespace(
    pose=types.SimpleNamespace(
        position=types.SimpleNamespace(x=1.0, y=-2.0),
        orientation=types.SimpleNamespace(x=0.0, y=0.0, z=0.7071, w=0.7071)))))
check("v3.7: sink /odom -> [x, y, yaw] telemetry values",
      sink.odom and abs(sink.odom[0] - 1.0) < 1e-6
      and abs(sink.odom[1] + 2.0) < 1e-6 and abs(sink.odom[2] - 1.5708) < 1e-3,
      str(sink.odom))

# (3) direct-rclpy map save from the cached /map grid (teammate ACCEPT).
_info37 = types.SimpleNamespace(
    width=2, height=2, resolution=0.05,
    origin=types.SimpleNamespace(position=types.SimpleNamespace(x=0.0, y=0.0),
                                 orientation=types.SimpleNamespace(
                                     x=0.0, y=0.0, z=0.0, w=1.0)))
nd9 = navd.NavDaemon(sink=None, post=None, dry=False, robot_id="shimbot")
nd9._map_msg = types.SimpleNamespace(info=_info37, data=[0, 100, -1, 50])
nd9._map_msg_ts = _time.monotonic()
check("v3.7: direct-rclpy map save writes trinary PGM (rows flipped) + yaml",
      nd9._save_map_python("cachedmap")
      and open(_os.path.join(_mapdir, "cachedmap.pgm"), "rb").read()
      == b"P5\n2 2\n255\n" + bytes([205, 205, 254, 0])
      and "free_thresh: 0.25" in open(_os.path.join(_mapdir, "cachedmap.yaml")).read())
nd9._map_msg = None
nd9._own_node = type("N", (), {
    "create_subscription": lambda s, t, topic, cb, qos: object(),
    "destroy_subscription": lambda s, sub: None})()
check("v3.7: no /map within timeout -> False so the CLI fallback takes over",
      nd9._save_map_python("nocache", timeout=0.3) is False)

# ================= v3.7 CS-B1: REAL action-client route path ================
# The P0 lesson: Humble rclpy.action.ClientGoalHandle exposes ONLY
# get_result_async(), whose future resolves to FollowWaypoints.GetResult
# .Response WRAPPING the .result (missed_waypoints). Dry-FSM tests never
# touched the action client, so every real route failed silently for
# releases. This section fakes the EXACT Humble surface — no legacy
# property-style future attribute anywhere on the fake — and drives a
# non-dry route all the way through _goal_cb -> _result_cb.
_mod("nav2_msgs")


class _FWGoal:
    def __init__(self):
        self.poses = []


class _FWResult:
    def __init__(self, missed):
        self.missed_waypoints = list(missed)
        self.return_code = 1


class _FWGetResult:
    class Response:
        def __init__(self, result=None):
            self.result = result


class _FollowWaypoints:
    Goal = _FWGoal
    GetResult = _FWGetResult


_mod("nav2_msgs.action", FollowWaypoints=_FollowWaypoints)


class _RF:                                  # minimal rclpy Future lookalike
    def __init__(self):
        self._cbs = []
        self._done = False
        self._res = None

    def add_done_callback(self, cb):
        self._cbs.append(cb)
        if self._done:
            cb(self)

    def _resolve(self, r):
        self._res = r
        self._done = True
        for cb in list(self._cbs):
            cb(self)

    def result(self):
        if not self._done:
            raise RuntimeError("in flight")
        return self._res


class _GoalHandle:
    """Humble ClientGoalHandle: .accepted + get_result_async(). Nothing."""
    MISSED = 0
    DELAY = 0.2

    def __init__(self):
        self.accepted = True

    def get_result_async(self):
        f = _RF()

        def _later():
            _time.sleep(self.DELAY)
            f._resolve(_FWGetResult.Response(
                result=_FWResult([0.0] * self.MISSED)))
        threading.Thread(target=_later, daemon=True).start()
        return f


class _ActionClient:
    def __init__(self, node, type_, name):
        self.name = name

    def wait_for_server(self, timeout_sec=0.0):
        return True

    def send_goal_async(self, goal, feedback_callback=None):
        f = _RF()
        f._resolve(_GoalHandle())
        return f


_mod("rclpy.action", ActionClient=_ActionClient)
sys.modules["geometry_msgs.msg"].PoseStamped = lambda: types.SimpleNamespace(
    header=types.SimpleNamespace(frame_id="", stamp=None),
    pose=types.SimpleNamespace(
        position=types.SimpleNamespace(x=0.0, y=0.0, z=0.0),
        orientation=types.SimpleNamespace(x=0.0, y=0.0, z=0.0, w=1.0)))

nd7 = navd.NavDaemon(sink=None, post=None, dry=False, robot_id="shimbot")
nd7._own_node = _Node()                     # clock stub for goal stamps
nd7.mode = "localizing"                     # stack already up
nd7._await_pose = lambda timeout=20.0: True  # pose-gate seam (tested above)
nd7._start_route([[1.0, 1.0, 0.0], [2.0, 2.0, 0.5]])
s = _wait_nav(nd7, lambda s: s["driving"], timeout=2.0)
check("CS-B1: real (non-dry) route sends goal, mode=route driving",
      s["driving"] and s["mode"] == "route", f"mode={s['mode']} drv={s['driving']}")
s = _wait_nav(nd7, lambda s: not s["driving"], timeout=4.0)
check("CS-B1: route COMPLETES via get_result_async (exact Humble API)",
      s["mode"] == "localizing" and not s["driving"] and not s.get("error"),
      str(s.get("error")))

_GoalHandle.MISSED = 2                      # missed-waypoint propagation
nd8 = navd.NavDaemon(sink=None, post=None, dry=False, robot_id="shimbot")
nd8._own_node = _Node()
nd8.mode = "localizing"
nd8._await_pose = lambda timeout=20.0: True
nd8._start_route([[1.0, 1.0, 0.0]])
s = _wait_nav(nd8, lambda s: s["error"], timeout=4.0)
check("CS-B1: missed_waypoints unwrap through GetResult.Response",
      s["error"] == "2 waypoint(s) missed", str(s.get("error")))

check("CS-B1: fake goal handle = Humble surface only (no legacy future attr)",
      not hasattr(_GoalHandle(), "result_" + "future"))
_banned = "result_" + "future"
_hits = sorted(p.name for p in (CLIENT.parent).glob("*.py")
               if _banned in p.read_text())
check("CS-B1: banned legacy future attribute absent from robot/*.py",
      not _hits, str(_hits))

print("RESULT:", "ALL CHECKS PASSED" if not failures else f"FAILURES: {failures}")
sys.exit(1 if failures else 0)
