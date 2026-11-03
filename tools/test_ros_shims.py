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
        NODES.append(self)

    def create_publisher(self, msgtype, topic, qos):
        pub = types.SimpleNamespace(topic=topic, sent=[])
        pub.publish = lambda m, p=pub: p.sent.append(m)
        return pub

    def create_subscription(self, msgtype, topic, cb, qos):
        self.subs.append((topic, cb))
        return object()


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
_mod("sensor_msgs.msg", Image=type("Image", (), {}))

# fake cv2: pass-through geometry, imencode hands back a marker payload
cv2 = _mod("cv2", COLOR_RGB2BGR=0, COLOR_RGBA2BGR=1, COLOR_BGRA2BGR=2,
           COLOR_GRAY2BGR=3, IMWRITE_JPEG_QUALITY=4,
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
else:
    print("SKIP  decode test (numpy not installed)")

print("RESULT:", "ALL CHECKS PASSED" if not failures else f"FAILURES: {failures}")
sys.exit(1 if failures else 0)
