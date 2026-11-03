#!/usr/bin/env python3
"""Robotless regression tests for the rclpy code paths of robotctl/client.py.

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
CLIENT = HERE.parent / "robotctl" / "client.py"

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

print("RESULT:", "ALL CHECKS PASSED" if not failures else f"FAILURES: {failures}")
sys.exit(1 if failures else 0)
