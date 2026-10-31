#!/usr/bin/env python3
"""Self-contained end-to-end test for wheeltec-webctl v2 (pull-mode).

Spawns as CHILD processes: fake MJPEG cam (:9103), the Django server
(:8100), a duplicate Django (:8111, flock guard), and TWO real robot
clients (robotctl/client.py in WEBCTL_DRY_RUN mode, velocities recorded to
sink files). Then drives the real HTTP APIs and verifies:

  - fleet listing, per-robot online detection, battery telemetry uplink
  - real client publishes ~10 Hz to its sink; commands reach it via long-poll
  - multi-robot isolation (command alpha, beta stays zero) + 404 unknown robot
  - per-robot token auth (wrong token 401, unknown robot 404)
  - serializer rejects garbage/out-of-range; empty body defaults zero
  - server-side command staleness (held cmd older than CMD_TIMEOUT -> zero)
  - e-stop overrides a fresh command and latches; /stop unlatches; cmd unlatches
  - CLIENT watchdog: server frozen (SIGSTOP) -> robot zeroes within WATCHDOG
    despite the last command having been non-zero
  - camera: client pushes JPEGs, browser-side /cam/<id> relays multipart
  - SPA + JS bundle served; single-process owner guard (flock 503)
  - offline detection after a robot client dies
then shuts everything down and exits. Safe to run repeatedly, leaves nothing.

Usage:  .venv/bin/python -u tools/integration_test.py
"""
import base64
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
BASE = "http://127.0.0.1:8100"
API = BASE + "/api/robot"

ROBOTS = "alpha:tok-alpha,beta:tok-beta"
TOKEN = {"alpha": "tok-alpha", "beta": "tok-beta"}

ENV = {
    **{k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"},
    "WEBCTL_ROBOTS": ROBOTS,
    "MAX_LIN": "0.5",
    "MAX_ROT": "0.7",
    "CMD_TIMEOUT": "0.5",
    "WEBCTL_AUTH_USER": "",
    "WEBCTL_AUTH_PASS": "",
    "DJANGO_SECRET_KEY": "itest",
    "DJANGO_DEBUG": "true",
    "ALLOWED_HOSTS": "127.0.0.1,localhost",
}

procs = {}          # name -> (Popen, logfh)
failures = []


def start(name, args, logname=None):
    os.makedirs(LOGDIR, exist_ok=True)
    fh = open(os.path.join(LOGDIR, logname or (name + ".log")), "wb")
    p = subprocess.Popen(args, cwd=ROOT, env=ENV, stdout=fh, stderr=subprocess.STDOUT,
                         start_new_session=True)
    procs[name] = (p, fh)
    return p


def pg_signal(name, sig):
    p, _ = procs[name]
    os.killpg(os.getpgid(p.pid), sig)


def req(url, data=None, headers=None, maxread=None, timeout=5):
    reqobj = urllib.request.Request(url, method="POST" if data is not None else "GET")
    reqobj.add_header("Accept", "application/json")
    for k, v in (headers or {}).items():
        reqobj.add_header(k, v)
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        reqobj.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(reqobj, body, timeout=timeout) as r:
            payload = r.read(maxread) if maxread else r.read()
            return r.status, r.headers, payload
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()
    except Exception as e:
        return 0, {}, str(e).encode()


def robot_auth(rid):
    tok = base64.b64encode(f"{rid}:{TOKEN.get(rid, 'nope')}".encode()).decode()
    return {"Authorization": f"Basic {tok}"}


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))
    if not cond:
        failures.append(name)


def wait_for(fn, timeout=5.0, every=0.15):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(every)
    return False


def get_status(rid=None):
    url = API if rid is None else f"{API}/{rid}"
    st, _, body = req(url)
    return json.loads(body) if st == 200 else None


def sink(rid):
    path = os.path.join(LOGDIR, f"sink-{rid}.json")
    try:
        with open(path) as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return None


def sink_is_zero(rid):
    s = sink(rid)
    return bool(s) and s["vx"] == 0.0 and s["wz"] == 0.0


def sink_cmd(rid, vx, wz=0.0, tol=1e-9, timeout=1.5):
    return wait_for(
        lambda: (lambda s: s and abs(s["vx"] - vx) <= tol and abs(s["wz"] - wz) <= tol)(sink(rid)),
        timeout)


def cleanup():
    for name, (p, fh) in procs.items():
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGCONT)  # in case frozen
        except Exception:
            pass
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            p.terminate()
        fh.close()


try:
    start("cam", [PY, "tools/fake_cam.py", "9103"])

    # fresh sinks so old assertions can never pass on stale files
    for rid in ("alpha", "beta"):
        pth = os.path.join(LOGDIR, f"sink-{rid}.json")
        if os.path.exists(pth):
            os.remove(pth)

    start("django", [PY, "backend/manage.py", "runserver", "127.0.0.1:8100", "--noreload"])
    time.sleep(2.0)

    def robot_env(rid, extra):
        return {**ENV, "WEBCTL_SERVER": BASE, "WEBCTL_ROBOT_ID": rid,
                "WEBCTL_ROBOT_TOKEN": TOKEN[rid], "WEBCTL_DRY_RUN": "1",
                "WEBCTL_WATCHDOG": "1.0", "WEBCTL_POLL_WAIT": "0.4",
                "WEBCTL_SINK": os.path.join(LOGDIR, f"sink-{rid}.json"), **extra}

    def start_robot(rid, extra):
        os.makedirs(LOGDIR, exist_ok=True)
        fh = open(os.path.join(LOGDIR, f"client-{rid}.log"), "wb")
        p = subprocess.Popen([PY, "robotctl/client.py"], cwd=ROOT,
                             env=robot_env(rid, extra), stdout=fh,
                             stderr=subprocess.STDOUT, start_new_session=True)
        procs[f"client-{rid}"] = (p, fh)

    start_robot("beta", {})                       # no camera
    start_robot("alpha", {"WEBCTL_CAM_URL": "http://127.0.0.1:9103/stream"})
    time.sleep(1.5)

    print("[1] fleet: listing, online detection, telemetry uplink")
    online_ok = wait_for(
        lambda: (d := get_status()) is not None
        and len(d.get("robots", [])) == 2
        and all(r["online"] for r in d["robots"]), 6)
    s = get_status()
    check("GET /api/robot lists both robots online",
          online_ok and s is not None
          and {r["id"] for r in s.get("robots", [])} == {"alpha", "beta"},
          str(s and [r["online"] for r in s.get("robots", [])]))
    wait_for(lambda: (d := get_status("alpha")) is not None
             and d["battery_v"] is not None, 6)
    wait_for(lambda: (d := get_status("alpha")) is not None and d["camera"], 6)
    sa = get_status("alpha")
    check("battery telemetry via client uplink", sa is not None
          and abs(sa["battery_v"] - 24.3) < 0.01, str(sa and sa["battery_v"]))
    check("status serialized (limits, applied, camera_path, camera hot)",
          sa is not None and sa["max_lin"] == 0.5 and sa["max_rot"] == 0.7
          and "vx" in sa["applied"] and sa["camera_path"] == "/cam/alpha"
          and sa["camera"] is True)
    sb = get_status("beta")
    check("beta has no camera (isolation of camera state)", sb and sb["camera"] is False)

    print("[2] command path: browser POST -> hub -> long-poll -> client sink @10Hz")
    st, _, body = req(API + "/alpha/cmd", {"vx": 0.4, "wz": 0.05})
    r = json.loads(body)
    check("POST /api/robot/<id>/cmd echoes applied values",
          st == 200 and abs(r["vx"] - 0.4) < 1e-9 and abs(r["wz"] - 0.05) < 1e-9, f"resp={r}")
    check("command reached alpha client via long-poll", sink_cmd("alpha", 0.4, 0.05))
    check("beta robot stayed stopped (multi-robot isolation)", sink_is_zero("beta"),
          str(sink("beta")))
    check("API clamps to MAX_LIN/MAX_ROT",
          req(API + "/alpha/cmd", {"vx": 5.0, "wz": -2.0})[0] == 200
          and abs(json.loads(req(API + "/alpha/cmd", {"vx": 5.0, "wz": -2.0})[2])["vx"] - 0.5) < 1e-9)
    check("clamp propagated to robot", sink_cmd("alpha", 0.5, -0.7))
    p1 = sink("alpha")["publishes"]
    time.sleep(1.0)
    p2 = sink("alpha")["publishes"]
    check("client publishes ~10 Hz", p2 - p1 >= 8, f"delta={p2 - p1}")

    print("[3] robot-facing auth")
    st, _, _ = req(BASE + "/robotctl/alpha/cmd?wait=0", headers=robot_auth("beta"))
    check("wrong token for alpha -> 401", st == 401, f"status={st}")
    st, _, _ = req(BASE + "/robotctl/gamma/cmd?wait=0", headers=robot_auth("alpha"))
    check("unknown robot id -> 404", st == 404, f"status={st}")
    st, _, _ = req(API + "/gamma")
    check("browser GET unknown robot -> 404", st == 404, f"status={st}")

    print("[4] serializer validation (browser -> server)")
    st, _, body = req(API + "/alpha/cmd", {"vx": "oops"})
    check("garbage velocity -> 400 with field errors", st == 400 and b"vx" in body, f"status={st}")
    st, _, _ = req(API + "/alpha/cmd", {"vx": 1e9})
    check("out-of-range velocity -> 400", st == 400, f"status={st}")
    st, _, body = req(API + "/alpha/cmd", {})
    check("empty body -> defaults 0,0 (200)", st == 200 and json.loads(body)["vx"] == 0.0)
    st, _, _ = req(BASE + "/robotctl/alpha/telemetry", {"battery_v": 999.0},
                   headers=robot_auth("alpha"))
    check("nonsense telemetry (999 V) -> 400", st == 400, f"status={st}")

    print("[5] server-side staleness (browser heartbeat stops)")
    req(API + "/alpha/cmd", {"vx": 0.3, "wz": 0.0})
    check("robot moving on fresh cmd", sink_cmd("alpha", 0.3))
    time.sleep(1.2)   # CMD_TIMEOUT is 0.5; no further browser refresh
    check("zero served after CMD_TIMEOUT without refresh", sink_is_zero("alpha"), str(sink("alpha")))

    print("[6] e-stop overrides fresh command, /stop unlatches")
    req(API + "/alpha/cmd", {"vx": 0.3, "wz": 0.1})
    check("motion resumed after new command", sink_cmd("alpha", 0.3, 0.1))
    req(API + "/alpha/estop", {})
    check("e-stop zeroes robot despite fresh cmd", sink_cmd("alpha", 0.0, 0.0, timeout=1.5))
    s = get_status("alpha")
    check("e-stop latched in status", s.get("estop") is True)
    req(API + "/alpha/cmd", {"vx": 0.2, "wz": 0.0})
    check("fresh cmd clears latch and drives", sink_cmd("alpha", 0.2))
    req(API + "/alpha/estop", {})
    wait_for(lambda: (get_status("alpha") or {}).get("estop") is True, 2)
    req(API + "/alpha/stop", {})
    s = get_status("alpha")
    check("POST /api/robot/<id>/stop clears e-stop latch", s.get("estop") is False)
    req(API + "/alpha/cmd", {"vx": 0.4, "wz": 0.0})
    check("driving works again after stop", sink_cmd("alpha", 0.4))

    print("[7] CLIENT watchdog: server frozen -> robot must stop by itself")
    pg_signal("django", signal.SIGSTOP)
    check("client zeroes within WATCHDOG of server freeze",
          wait_for(lambda: sink_is_zero("alpha") and sink("alpha")["watchdog_trips"] >= 1, 2.5),
          str(sink("alpha")))
    pg_signal("django", signal.SIGCONT)
    check("status endpoint alive again after unfreeze",
          wait_for(lambda: get_status("alpha") is not None, 4))

    print("[8] camera: client pushes frames, server relays to browser")
    st, hdr, body = req(BASE + "/cam/alpha", maxread=512)  # partial read only!
    check("MJPEG relay at /cam/<id>", st == 200
          and b"multipart/x-mixed-replace" in hdr.get("Content-Type", "").encode()
          and body.startswith(b"--frame"), f"status={st} ctype={hdr.get('Content-Type')}")
    st, _, body = req(BASE + "/cam/beta", timeout=8)
    check("/cam/<id> 503 for robot without camera", st == 503, f"status={st}")

    print("[9] frontend serving")
    st, _, body = req(BASE + "/")
    html = body.decode("utf-8", "replace")
    check("SPA index served by Django", st == 200 and 'id="root"' in html)
    asset = [seg for seg in html.split('"') if seg.startswith("/assets/") and seg.endswith(".js")]
    st2, _, _ = req(BASE + asset[0]) if asset else (404, None, b"")
    check("JS bundle served (WhiteNoise)", bool(asset) and st2 == 200, f"asset={asset[:1]}")

    print("[10] duplicate-process guard (flock)")
    start("django2", [PY, "backend/manage.py", "runserver", "127.0.0.1:8111", "--noreload"])
    time.sleep(2.0)
    st, _, body = req("http://127.0.0.1:8111/api/robot/alpha/cmd", {"vx": 0.1, "wz": 0.0})
    check("second worker refuses commands (503)", st == 503, f"status={st} body={body[:80]!r}")
    st, _, _ = req("http://127.0.0.1:8111/robotctl/alpha/cmd?wait=0", headers=robot_auth("alpha"))
    check("second worker refuses robot polls too (503)", st == 503, f"status={st}")

    print("[11] offline detection")
    pg_signal("client-alpha", signal.SIGTERM)
    ok = wait_for(lambda: (s := get_status("alpha")) and s["online"] is False, 5)
    check("alpha shows OFFLINE after client dies", ok,
          str(get_status("alpha") and get_status("alpha")["online"]))
    s = get_status("beta")
    check("beta unaffected (still online)", s and s["online"] is True)

    print()
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
finally:
    cleanup()
