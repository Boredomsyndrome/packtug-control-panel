#!/usr/bin/env python3
"""Self-contained end-to-end test for wheeltec-webctl (Django 6.1 + DRF API).

Starts fake rosbridge + fake MJPEG cam + the Django server as CHILD processes,
drives the real HTTP API, verifies:
  - bridge connects and receives battery telemetry
  - GET /api/robot status snapshot via DRF serializer
  - POST /api/robot/cmd velocity clamping (5 m/s -> MAX_LIN)
  - serializer rejects garbage/out-of-range bodies with 400; empty body defaults
  - 10 Hz publish to /cmd_vel actually reaches rosbridge
  - watchdog zeroes velocity when commands lapse
  - e-stop overrides a fresh command and latches; POST /stop unlatches
  - /cam proxies the MJPEG stream
  - Django serves the built React page + JS bundle
  - a second server process is refused (single bridge owner)
then shuts everything down and exits. Safe to run repeatedly, leaves nothing
running.

Usage:  .venv/bin/python -u tools/integration_test.py
"""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv", "bin", "python")
LOGDIR = os.path.join(ROOT, ".logs")
API = "http://127.0.0.1:8000/api/robot"

ENV = {
    **{k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"},
    "ROBOT_WS_URL": "ws://127.0.0.1:9091",
    "CAMERA_BASE_URL": "http://127.0.0.1:9093",
    "CAMERA_TOPIC": "/image_raw",
    "BATTERY_TOPIC": "",
    "CMD_VEL_TOPIC": "/cmd_vel",
    "MAX_LIN": "0.5",
    "MAX_ROT": "0.7",
    "CMD_TIMEOUT": "0.5",
    "WEBCTL_AUTH_USER": "",
    "WEBCTL_AUTH_PASS": "",
    "DJANGO_SECRET_KEY": "itest",
    "DJANGO_DEBUG": "true",
    "ALLOWED_HOSTS": "127.0.0.1,localhost",
}

procs = []
failures = []


def start(args, logname):
    os.makedirs(LOGDIR, exist_ok=True)
    fh = open(os.path.join(LOGDIR, logname), "wb")
    p = subprocess.Popen(args, cwd=ROOT, env=ENV, stdout=fh, stderr=subprocess.STDOUT,
                         start_new_session=True)
    procs.append((p, fh))
    return p


def cleanup():
    for p, fh in procs:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            p.terminate()
        fh.close()


def req(url, data=None, maxread=None):
    reqobj = urllib.request.Request(url, method="POST" if data is not None else "GET")
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        reqobj.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(reqobj, body, timeout=5) as r:
            # maxread: never read() an endless stream (MJPEG) fully
            payload = r.read(maxread) if maxread else r.read()
            return r.status, r.headers, payload
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))
    if not cond:
        failures.append(name)


def get_status():
    st, _, body = req(API)
    return json.loads(body) if st == 200 else None


def fake_last():
    _, _, body = req("http://127.0.0.1:9092")
    return json.loads(body)["last_cmd"]


try:
    start([PY, "tools/fake_rosbridge.py"], "fake_ros.log")
    start([PY, "tools/fake_cam.py"], "fake_cam.log")
    start([PY, "backend/manage.py", "runserver", "127.0.0.1:8000", "--noreload"], "django.log")
    time.sleep(2.0)

    print("[1] connection + telemetry (GET /api/robot via DRF serializer)")
    s = None
    end = time.time() + 8
    while time.time() < end:
        s = get_status()
        if s and s.get("connected") and s.get("battery_v") is not None:
            break
        time.sleep(0.3)
    check("bridge connected to rosbridge", bool(s and s.get("connected")), str(s and s.get("last_error")))
    check("battery telemetry received", s is not None and s.get("battery_v") is not None
          and abs(s["battery_v"] - 24.3) < 0.01,
          f"got {s and s.get('battery_v')} on {s and s.get('battery_topic')}")
    check("status serialized (nested cmd + limits present)",
          s is not None and isinstance(s.get("cmd"), dict) and "vx" in s["cmd"]
          and s.get("max_lin") == 0.5 and s.get("camera_path") == "/cam")

    print("[2] command + clamp + 10Hz publish (POST /api/robot/cmd)")
    st, _, body = req(API + "/cmd", {"vx": 5.0, "wz": -2.0})
    r = json.loads(body)
    check("API clamps to MAX_LIN/MAX_ROT", st == 200 and abs(r["vx"] - 0.5) < 1e-9 and abs(r["wz"] + 0.7) < 1e-9,
          f"resp={r}")
    time.sleep(0.35)
    last = fake_last()
    check("Twist reached rosbridge /cmd_vel", abs(last["vx"] - 0.5) < 1e-9 and abs(last["wz"] + 0.7) < 1e-9,
          f"fake saw {last}")
    check("publish rate ~10Hz", last["n"] >= 3, f"n={last['n']}")

    print("[2b] serializer validation")
    st, _, body = req(API + "/cmd", {"vx": "oops"})
    check("garbage velocity -> 400 with field errors", st == 400 and b"vx" in body, f"status={st}")
    st, _, body = req(API + "/cmd", {"vx": 1e9})
    check("out-of-range velocity -> 400", st == 400, f"status={st}")
    st, _, body = req(API + "/cmd", {})
    check("empty body -> defaults 0,0 (200)", st == 200 and json.loads(body)["vx"] == 0.0)

    print("[3] watchdog (no fresh command)")
    time.sleep(1.0)
    last = fake_last()
    check("watchdog publishes ZERO after CMD_TIMEOUT", last["vx"] == 0.0 and last["wz"] == 0.0, f"fake saw {last}")

    print("[4] e-stop overrides fresh command, /stop unlatches")
    req(API + "/cmd", {"vx": 0.3, "wz": 0.1})
    time.sleep(0.3)
    last = fake_last()
    check("motion resumed after new command", abs(last["vx"] - 0.3) < 1e-9, f"fake saw {last}")
    req(API + "/estop", {})
    time.sleep(0.3)
    last = fake_last()
    s = get_status()
    check("e-stop zeroes robot despite fresh cmd", last["vx"] == 0.0 and last["wz"] == 0.0, f"fake saw {last}")
    check("e-stop latched in status", s.get("estop") is True)
    req(API + "/stop", {})
    s = get_status()
    check("POST /api/robot/stop clears e-stop latch", s.get("estop") is False)
    req(API + "/cmd", {"vx": 0.4, "wz": 0.0})
    time.sleep(0.3)
    last = fake_last()
    check("driving works again after stop", abs(last["vx"] - 0.4) < 1e-9, f"fake saw {last}")

    print("[5] camera proxy")
    st, hdr, body = req("http://127.0.0.1:8000/cam", maxread=512)  # partial read only!
    check("MJPEG proxied via /cam", st == 200 and b"multipart/x-mixed-replace" in hdr.get("Content-Type", "").encode()
          and body.startswith(b"--frame"), f"status={st} ctype={hdr.get('Content-Type')}")

    print("[6] frontend serving")
    st, _, body = req("http://127.0.0.1:8000/")
    html = body.decode("utf-8", "replace")
    check("SPA index served by Django", st == 200 and 'id="root"' in html)
    asset = [seg for seg in html.split('"') if seg.startswith("/assets/") and seg.endswith(".js")]
    st2, _, _ = req("http://127.0.0.1:8000" + asset[0]) if asset else (404, None, b"")
    check("JS bundle served (WhiteNoise)", bool(asset) and st2 == 200, f"asset={asset[:1]}")

    print("[7] duplicate-process guard (flock)")
    start([PY, "backend/manage.py", "runserver", "127.0.0.1:8011", "--noreload"], "django2.log")
    time.sleep(2.0)
    st, _, body = req("http://127.0.0.1:8011/api/robot/cmd", {"vx": 0.1, "wz": 0.0})
    check("second worker refuses commands (single bridge owner)", st == 503, f"status={st} body={body[:80]!r}")

    print()
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
finally:
    cleanup()
