#!/usr/bin/env python3
"""Self-contained end-to-end test for wheeltec-webctl v3 (pull-mode + WS).

Spawns as CHILD processes: fake MJPEG cam (:9103), the ASGI server (uvicorn,
:8100), a duplicate server (:8111, flock guard), and TWO real robot clients
(robotctl/client.py, WEBCTL_DRY_RUN, velocities recorded to sink files):
  alpha -> WebSocket transport (auto) + MJPEG camera pushed as ws frames
  beta  -> forced HTTP long-poll transport, no camera
plus a raw protocol probe on robot 'gamma' (empty-token dev robot).

Verifies:
  - fleet listing, online detection, transports (ws/http), battery uplink
  - browser Basic Auth vs per-robot ws tokens: two credential systems, no mix
  - command PUSH latency over ws (<150 ms), staleness auto-zero pushed
  - probe ws protocol: welcome, cmd push, telemetry validation, takeover
  - camera: robot frame -> viewer ws (ts prefix) AND legacy /cam/<id> relay
  - commands reach the http-transport client via long-poll (fallback works)
  - clamp/serializer/404/401/400s (server-side safety, transport-independent)
  - e-stop latch/unlatch on both transports; ~10 Hz publish rate
  - CLIENT watchdog: SIGSTOP the whole server -> robot zeroes itself, both
    transports reconnect on resume
  - SPA + bundle; flock 503 (http + ws); offline detection after client dies
then shuts everything down. Safe to run repeatedly.

Usage:  .venv/bin/python -u tools/integration_test.py
"""
import base64
import fcntl
import json
import os
import signal
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv", "bin", "python")
LOGDIR = os.path.join(ROOT, ".logs")
BASE = "http://127.0.0.1:8100"
API = BASE + "/api/robot"

sys.path.insert(0, os.path.join(ROOT, "robotctl"))
os.environ.setdefault("WEBCTL_SERVER", BASE)
from client import StdWs                                   # noqa: E402

ROBOTS = "alpha:tok-alpha,beta:tok-beta,gamma:"
TOKEN = {"alpha": "tok-alpha", "beta": "tok-beta", "gamma": ""}
BROWSER = ("itest", "itest-pw")

ENV = {
    **{k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"},
    "WEBCTL_ROBOTS": ROBOTS,
    "MAX_LIN": "0.5", "MAX_ROT": "0.7", "CMD_TIMEOUT": "0.5",
    "WEBCTL_AUTH_USER": BROWSER[0], "WEBCTL_AUTH_PASS": BROWSER[1],
    "DJANGO_SECRET_KEY": "itest", "DJANGO_DEBUG": "true",
    "ALLOWED_HOSTS": "127.0.0.1,localhost",
}

procs = {}
failures = []


def start(name, args):
    os.makedirs(LOGDIR, exist_ok=True)
    fh = open(os.path.join(LOGDIR, name + ".log"), "wb")
    p = subprocess.Popen(args, cwd=ROOT, env=ENV, stdout=fh,
                         stderr=subprocess.STDOUT, start_new_session=True)
    procs[name] = (p, fh)
    return p


def pg_signal(name, sig):
    p, _ = procs[name]
    os.killpg(os.getpgid(p.pid), sig)


def req(url, data=None, headers=None, maxread=None, timeout=5, auth=True):
    r = urllib.request.Request(url, method="POST" if data is not None else "GET")
    r.add_header("Accept", "application/json")
    if auth and BROWSER:
        r.add_header("Authorization", "Basic " + base64.b64encode(
            f"{BROWSER[0]}:{BROWSER[1]}".encode()).decode())
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, body, timeout=timeout) as resp:
            if maxread:
                # read1-style best-effort: resp.read(n) blocks until n bytes
                # arrive — wrong for endless multipart streams. Collect what
                # the wire actually gives us, up to maxread bytes.
                data = b""
                while len(data) < maxread:
                    chunk = resp.read1(65536) if hasattr(resp, "read1") \
                        else resp.read(maxread - len(data))
                    if not chunk:
                        break
                    data += chunk
                return resp.status, resp.headers, data
            return resp.status, resp.headers, resp.read()
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


def wait_for(fn, timeout=5.0, every=0.05):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(every)
    return False


def fleet(rid=None):
    url = API if rid is None else f"{API}/{rid}"
    st, _, body = req(url)
    if st != 200:
        return None
    d = json.loads(body)
    return d if rid is None else d


def status(rid):
    d = fleet(rid)
    return d if d and "id" in d else None


def robots_map():
    d = fleet()
    return {r["id"]: r for r in d["robots"]} if d else {}


def sink(rid):
    try:
        with open(os.path.join(LOGDIR, f"sink-{rid}.json")) as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return None


def sink_is_zero(rid):
    s = sink(rid)
    return bool(s) and s["vx"] == 0.0 and s["wz"] == 0.0


def sink_cmd(rid, vx, wz=0.0, tol=1e-9, timeout=2.0):
    return wait_for(lambda: (lambda s: s and abs(s["vx"] - vx) <= tol
                             and abs(s["wz"] - wz) <= tol)(sink(rid)), timeout)


def uvicorn(port):
    return [PY, "-m", "uvi" + "corn", "webctl.asgi:application",
            "--app-dir", "backend", "--host", "127.0.0.1",
            "--port", str(port), "--no-access-log"]


def ws_url(path, base=BASE):
    return base.replace("http://", "ws://") + path


def ws_recv_json(ws, timeout=2.0, types=None):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        op, payload = ws.recv(0.5)
        if op == "text":
            m = json.loads(payload)
            if types is None or m.get("type") in types:
                return m
        elif op == "close":
            return None
    return None


def cleanup():
    import errno as _errno
    for name, (p, fh) in procs.items():
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGCONT)
        except Exception:
            pass
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            p.terminate()
    deadline = time.time() + 4.0
    for name, (p, fh) in procs.items():
        try:
            p.wait(timeout=max(0.1, deadline - time.time()))
        except Exception:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                p.kill()
        fh.close()


def preflight_lock():
    """Fail loudly if another server still owns .server.lock (leaked run)."""
    path = os.path.join(ROOT, ".server.lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        if e.errno in (_errno.EAGAIN, _errno.EACCES):
            holder = open(path).read().strip()
            sys.exit(f"ABORT: .server.lock held by pid {holder} — kill it first")
        raise
    finally:
        os.close(fd)   # release; the real server will claim it


preflight_lock()
try:
    start("cam", [PY, "tools/fake_cam.py", "9103"])
    for rid in ("alpha", "beta", "gamma"):
        pth = os.path.join(LOGDIR, f"sink-{rid}.json")
        if os.path.exists(pth):
            os.remove(pth)

    start("asgi", uvicorn(8100))
    time.sleep(2.5)

    def start_robot(rid, extra):
        env = {**ENV, "WEBCTL_SERVER": BASE, "WEBCTL_ROBOT_ID": rid,
               "WEBCTL_ROBOT_TOKEN": TOKEN[rid], "WEBCTL_DRY_RUN": "1",
               "WEBCTL_WATCHDOG": "1.0", "WEBCTL_POLL_WAIT": "0.3",
               "WEBCTL_WS_PING_INTERVAL": "0.5",
               "WEBCTL_SINK": os.path.join(LOGDIR, f"sink-{rid}.json"), **extra}
        fh = open(os.path.join(LOGDIR, f"client-{rid}.log"), "wb")
        p = subprocess.Popen([PY, "robotctl/client.py"], cwd=ROOT, env=env,
                             stdout=fh, stderr=subprocess.STDOUT,
                             start_new_session=True)
        procs[f"client-{rid}"] = (p, fh)

    start_robot("beta", {"WEBCTL_TRANSPORT": "http"})
    start_robot("alpha", {"WEBCTL_CAM_MODE": "mjpeg",
                          "WEBCTL_CAM_URL": "http://127.0.0.1:9103/stream",
                          "WEBCTL_CAM_FPS": "20"})
    time.sleep(2.5)

    print("[1] fleet, transports, telemetry uplink (ws + http)")
    check("browser Basic Auth enforced (401 without creds)",
          req(BASE + "/api/robot", auth=False)[0] == 401)
    online_ok = wait_for(lambda: (m := robots_map()) and len(m) == 3
                         and m["alpha"]["online"] and m["beta"]["online"], 8)
    m = robots_map()
    check("alpha (ws) and beta (http) online, gamma idle offline",
          online_ok and not m.get("gamma", {}).get("online"))
    check("transport reporting: alpha=ws, beta=http",
          m["alpha"]["transport"] == "ws" and m["beta"]["transport"] == "http",
          f'{m["alpha"]["transport"]}/{m["beta"]["transport"]}')
    check("battery via ws telemetry", abs((m["alpha"]["battery_v"] or 0) - 24.3) < 0.01,
          str(m["alpha"]["battery_v"]))
    check("battery via http telemetry", abs((m["beta"]["battery_v"] or 0) - 24.3) < 0.01)
    sa = status("alpha")
    check("status fields incl. camera_path + camera hot (ws-pushed frames)",
          sa["camera_path"] == "/cam/alpha" and sa["camera"] is True
          and sa["max_lin"] == 0.5 and sa["max_rot"] == 0.7)
    check("beta has no camera (state isolation)", status("beta")["camera"] is False)

    print("[2] ws protocol probe (gamma): welcome, push latency, telemetry")
    probe = StdWs(ws_url("/ws/robotctl/gamma?token="), timeout=5)
    probe.connect()
    w = ws_recv_json(probe, types={"welcome"})
    check("welcome carries zero-safe cmd snapshot",
          w and w["vx"] == 0.0 and w["wz"] == 0.0 and "seq" in w)
    t0 = time.monotonic()
    st, _, body = req(API + "/gamma/cmd", {"vx": 0.3, "wz": 0.2})
    pushed = ws_recv_json(probe, types={"cmd"})
    dt = time.monotonic() - t0
    check("cmd pushed over ws within 150 ms of browser POST",
          pushed and abs(pushed["vx"] - 0.3) < 1e-9 and dt < 0.15,
          f"{dt*1000:.0f}ms")
    probe.send(json.dumps({"type": "telemetry", "battery_v": 26.6,
                           "applied_vx": 0.3, "applied_wz": 0.2,
                           "watchdog_trips": 0}))
    check("ws telemetry visible in fleet", wait_for(
        lambda: abs((robots_map()["gamma"].get("battery_v") or 0) - 26.6) < 0.01))
    probe.send(json.dumps({"type": "telemetry", "battery_v": 999.0}))
    time.sleep(0.4)
    check("garbage ws telemetry rejected (battery unchanged)",
          abs((robots_map()["gamma"].get("battery_v") or 0) - 26.6) < 0.01)

    print("[3] ws staleness push: held cmd expires -> robot gets zero unsolicited")
    time.sleep(1.0)  # CMD_TIMEOUT 0.5 passed with nobody refreshing
    m = ws_recv_json(probe, types={"cmd"}, timeout=2.0)
    check("stale cmd auto-zero pushed over ws", m is not None and m["vx"] == 0.0,
          str(m))

    print("[4] camera over ws + legacy HTTP relay")
    probe.send(bytes.fromhex("ffd8") + b"probe-frame" + bytes.fromhex("ffd9"), binary=True)
    viewer = StdWs(ws_url("/ws/cam/gamma"), timeout=5)
    viewer.connect()
    blob = None
    end = time.monotonic() + 3.0
    while time.monotonic() < end and blob is None:
        op, payload = viewer.recv(0.5)
        if op == "binary" and len(payload) > 10:
            blob = payload
    check("viewer ws receives frame with 8-byte server ts prefix",
          blob is not None and blob[8:10] == b"\xff\xd8")
    if blob:
        ts = struct.unpack("<d", blob[:8])[0]
        check("viewer latency plausible (<2 s)", time.time() - ts < 2.0)
    st, hdr, body = req(BASE + "/cam/alpha", maxread=512, timeout=6)
    check("legacy /cam/alpha HTTP relay still works",
          st == 200 and b"multipart/x-mixed-replace" in str(hdr.get("Content-Type", "")).encode()
          and body.startswith(b"--frame"),
          f"status={st} body={body[:90]!r}")
    st, _, _ = req(BASE + "/cam/beta", timeout=8)
    check("/cam 503 for camera-less robot", st == 503, f"status={st}")

    print("[5] takeover + ws auth")
    probe2 = StdWs(ws_url("/ws/robotctl/gamma?token="), timeout=5)
    probe2.connect()
    took = False
    end = time.monotonic() + 3.0
    while time.monotonic() < end and not took:
        op, payload = probe.recv(0.5)
        if op == "text":
            took = json.loads(payload).get("type") == "takeover"
        elif op == "close":
            took = True
    check("new connection takes over robot slot (old notified)", took)
    probe2.close()
    refused = False
    try:
        bad = StdWs(ws_url("/ws/robotctl/alpha?token=WRONG"), timeout=4)
        bad.connect()
        bad.close()
    except Exception:
        refused = True
    check("ws handshake refused for bad token", refused)
    st, _, _ = req(BASE + "/robotctl/alpha/cmd?wait=0", headers=robot_auth("beta"))
    check("http robot auth: wrong token -> 401", st == 401, f"status={st}")
    st, _, _ = req(API + "/nosuchrobot")
    check("unknown robot -> 404", st == 404)

    print("[6] http-transport client applies cmds (fallback path)")
    req(API + "/beta/cmd", {"vx": 0.25, "wz": 0.05})
    check("beta cmd applied via http long-poll", sink_cmd("beta", 0.25, 0.05, tol=1e-6))
    check("alpha untouched (isolation across transports)", sink_is_zero("alpha"))
    st, _, body = req(API + "/beta/cmd", {"vx": 5.0, "wz": -2.0})
    check("API clamps to limits", json.loads(body)["vx"] == 0.5, str(body[:60]))
    check("clamped cmd reaches http robot", sink_cmd("beta", 0.5, -0.7, tol=1e-6))
    p1 = sink("alpha")["publishes"]
    time.sleep(1.0)
    p2 = sink("alpha")["publishes"]
    check("ws client publishes ~10 Hz", p2 - p1 >= 8, f"delta={p2-p1}")

    print("[7] serializer validation")
    st, _, body = req(API + "/beta/cmd", {"vx": "oops"})
    check("garbage velocity -> 400", st == 400 and b"vx" in body)
    st, _, _ = req(API + "/beta/cmd", {"vx": 1e9})
    check("out-of-range -> 400", st == 400)
    st, _, body = req(API + "/beta/cmd", {})
    check("empty body -> zero (200)", st == 200 and json.loads(body)["vx"] == 0.0)

    print("[8] staleness + e-stop on the http transport")
    req(API + "/beta/cmd", {"vx": 0.3, "wz": 0.0})
    check("beta moving", sink_cmd("beta", 0.3, tol=1e-6))
    time.sleep(1.2)
    check("beta zeroed by server staleness", sink_is_zero("beta"), str(sink("beta")))
    req(API + "/beta/cmd", {"vx": 0.3, "wz": 0.1})
    req(API + "/beta/estop", {})
    check("e-stop zeroes despite fresh cmd", sink_cmd("beta", 0.0, 0.0))
    check("e-stop latched", status("beta")["estop"] is True)
    time.sleep(0.8)
    check("latched through staleness", sink_is_zero("beta"))
    req(API + "/beta/cmd", {"vx": 0.2, "wz": 0.0})
    check("fresh cmd unlatches", sink_cmd("beta", 0.2, tol=1e-6))
    req(API + "/beta/estop", {})
    wait_for(lambda: status("beta")["estop"] is True, 2)
    req(API + "/beta/stop", {})
    check("/stop unlatches", status("beta")["estop"] is False)

    print("[9] CLIENT watchdog: freeze server -> BOTH robots stop themselves")
    # regression: pings must keep 'contact' fresh — a ws client sitting on an
    # unchanged command for several seconds must NOT trip its watchdog
    base_trips = sink("alpha")["watchdog_trips"]
    time.sleep(4.0)
    s = sink("alpha")
    check("steady state: no spurious watchdog trips on idle ws link",
          s["watchdog_trips"] == base_trips and s["state"] != "watchdog",
          f"trips {base_trips}->{s['watchdog_trips']}")
    req(API + "/alpha/cmd", {"vx": 0.3, "wz": 0.0})
    pg_signal("asgi", signal.SIGSTOP)   # freeze NOW: contact-loss is the trigger
    check("ws client watchdog trips and zeroes",
          wait_for(lambda: sink("alpha")["watchdog_trips"] >= 1
                   and sink("alpha")["vx"] == 0.0, 3.0), str(sink("alpha")))
    check("http client watchdog trips and zeroes",
          wait_for(lambda: sink("beta")["watchdog_trips"] >= 1, 3.0))
    pg_signal("asgi", signal.SIGCONT)
    check("ws client reconnects after unfreeze", wait_for(
        lambda: robots_map().get("alpha", {}).get("online"), 12))
    check("http client reconnects after unfreeze", wait_for(
        lambda: robots_map().get("beta", {}).get("online"), 12))
    check("ws transport restored", wait_for(
        lambda: robots_map()["alpha"]["transport"] == "ws", 8))

    print("[9b] lidar: synthetic scans uplink (ws + http) and serve via API")
    scan_ok = wait_for(lambda: (mm := robots_map()) and mm["alpha"]["scan"]
                       and mm["beta"]["scan"], 8)
    m2 = robots_map()
    check("alpha(ws) + beta(http) lidar live, gamma idle has none",
          scan_ok and m2["gamma"]["scan"] is False, str(m2.get("beta", {}).get("scan_age")))
    st, _, body = req(f"{API}/alpha/scan")
    d = json.loads(body)
    rs = d.get("scan") or {}
    check("GET /api/robot/alpha/scan: fresh packed ring",
          st == 200 and d["ok"] and d["fresh"] and rs
          and 1 <= len(rs["ranges"]) <= 4096
          and all(isinstance(r, (int, float)) and 0.0 <= r <= 1000.0
                  for r in rs["ranges"]) and rs["amax"] > rs["amin"],
          f"{len(rs.get('ranges', []))} rays")
    st, _, body = req(f"{API}/gamma/scan")
    d = json.loads(body)
    check("offline robot: scan endpoint answers ok+not-fresh (no exception)",
          st == 200 and d["ok"] and d["fresh"] is False and d["scan"] is None)
    good_scan = {"type": "scan", "frame": "laser", "amin": -3.14, "amax": 3.14,
                 "ainc": 0.02, "rmin": 0.15, "rmax": 8.0,
                 "ranges": [0.0, 1.5, 2.25, 3.0, 0.0, 8.0]}
    st, _, _ = req(BASE + "/robotctl/gamma/scan", data=good_scan, auth=False,
                   headers=robot_auth("gamma"))
    st2, _, body2 = req(f"{API}/gamma/scan")
    d2 = json.loads(body2)
    check("POST /robotctl/gamma/scan (http ingest) then API shows it fresh",
          st == 200 and st2 == 200 and d2["fresh"] is True
          and d2["scan"]["ranges"] == good_scan["ranges"])
    st, _, _ = req(BASE + "/robotctl/gamma/scan",
                   data={"amin": 3.0, "amax": -3.0, "ainc": 0.01, "rmin": 0,
                         "rmax": 8, "ranges": [1.0]},
                   auth=False, headers=robot_auth("gamma"))
    check("scan validation: amax<=amin -> 400", st == 400)
    st, _, _ = req(BASE + "/robotctl/gamma/scan",
                   data=good_scan, auth=False, headers=robot_auth("beta"))
    check("scan ingest requires the RIGHT robot token -> 401", st == 401)
    check("status carries diagnostics fields (ros_topics, robot_cam_ok)",
          "ros_topics" in m2["alpha"] and "robot_cam_ok" in m2["alpha"])

    print("[10] SPA & single-owner guard")
    st, _, body = req(BASE + "/")
    html = body.decode("utf-8", "replace")
    check("SPA index served", st == 200 and 'id="root"' in html)
    asset = [seg for seg in html.split('"') if seg.startswith("/assets/") and seg.endswith(".js")]
    check("JS bundle served", bool(asset) and req(BASE + asset[0])[0] == 200)
    start("asgi-dup", uvicorn(8111))
    time.sleep(2.5)
    st, _, _ = req("http://127.0.0.1:8111/api/robot/alpha/cmd", {"vx": 0.1, "wz": 0},
                   timeout=8)
    check("dup server: browser cmd 503 (flock)", st == 503, f"status={st}")
    st, _, _ = req("http://127.0.0.1:8111/robotctl/alpha/cmd?wait=0",
                   headers=robot_auth("alpha"))
    check("dup server: robot poll 503", st == 503, f"status={st}")
    refused = False
    try:
        w2 = StdWs(ws_url("/ws/robotctl/gamma?token=",
                          base="http://127.0.0.1:8111"), timeout=4)
        w2.connect()
        w2.close()
    except Exception:
        refused = True
    check("dup server: robot ws refused (no hub)", refused)

    print("[11] offline detection")
    pg_signal("client-beta", signal.SIGTERM)
    check("beta OFFLINE after client dies", wait_for(
        lambda: robots_map()["beta"]["online"] is False, 6))
    check("alpha still online", robots_map()["alpha"]["online"] is True)

    print()
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
finally:
    cleanup()
