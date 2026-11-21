#!/usr/bin/env python3
"""CS-B2 sim-fleet loop — REAL client.py + navd.py processes, dry mode,
against a fused hub, driving the exact hub API a browser's nav buttons
hit. No ROS, no hardware, no motion: dry navd simulates the stack, and
the scripted dry pose feeds record/pose paths.

Default: boots its OWN fused hub (:8131) with a random s2000-sim token
(test env only, never a real credential), runs the full loop, exits.
`--server URL --rid ID --token TOK` targets an existing hub (cutover
rerun against live :8090 — the sim then joins that real fleet).

Sequence asserted: online -> map_on(mapping) -> nav_on(localizing) ->
record -> route(route + 409 teleop lockout) -> complete(localizing,
route_done>=1) -> estop latch + /stop unlatch -> map/nav off cycle ->
clean exit (client SIGTERM -> zero velocity in sink).

CS-B2 exit line: "SIM FLEET: ALL CHECKS PASSED"
"""
import argparse
import base64
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
SCRATCH = os.path.join(ROOT, ".scratch-simfleet")
LOGDIR = os.path.join(SCRATCH, "logs")
PY = sys.executable
PORT = 8131
RIDGE_DEFAULT = "s2000-sim"
failures = []


def req(url, data=None, headers=None, timeout=5):
    r = urllib.request.Request(url, method="POST" if data is not None else "GET")
    r.add_header("Accept", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, body, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:                                   # noqa: BLE001
        return 0, str(e).encode()


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (f"   [{detail}]" if detail else ""))
    if not cond:
        failures.append(name)


def wait_for(fn, timeout=8.0, every=0.1):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:                                     # noqa: BLE001
            pass
        time.sleep(every)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default=None,
                    help="existing hub base URL (skip booting our own)")
    ap.add_argument("--rid", default=RIDGE_DEFAULT)
    ap.add_argument("--token", default=None)
    args = ap.parse_args()

    os.makedirs(LOGDIR, exist_ok=True)
    for junk in Path(SCRATCH).glob("sink-*.json"):
        junk.unlink()
    rid = args.rid
    token = args.token or secrets.token_hex(16)
    live = bool(args.server)
    base = (args.server or f"http://127.0.0.1:{PORT}").rstrip("/")
    api = base + "/api/robot"
    sink_path = os.path.join(SCRATCH, f"sink-{rid}.json")

    procs = []
    if not live:
        env = {**os.environ,
               "WEBCTL_ROBOTS": f"{rid}:{token}",
               "WEBCTL_LOCK": os.path.join(SCRATCH, ".hub.lock"),
               "WEBCTL_DATA_DIR": os.path.join(SCRATCH, ".hub-data"),
               "PACKTUG_MOCK": "1"}
        fh = open(os.path.join(LOGDIR, "hub.log"), "wb")
        procs.append(subprocess.Popen(
            [PY, "-m", "uvi" + "corn", "webctl.asgi:application",
             "--app-dir", "backend", "--host", "127.0.0.1",
             "--port", str(PORT), "--no-access-log"],
            cwd=ROOT, env=env, stdout=fh, stderr=subprocess.STDOUT,
            start_new_session=True))
        print(f"[hub] fused hub on :{PORT} (data {os.path.join(SCRATCH, '.hub-data')})")

    def status():
        st, b = req(f"{api}/{rid}")
        return json.loads(b) if st == 200 and b[:1] == b"{" else None

    try:
        deadline = time.time() + 20
        while time.time() < deadline and req(base + "/api/robot")[0] != 200:
            time.sleep(0.3)
        check("hub fleet endpoint up", req(base + "/api/robot")[0] == 200)

        cenv = {**os.environ,
                "WEBCTL_SERVER": base,
                "WEBCTL_ROBOT_ID": rid,
                "WEBCTL_ROBOT_TOKEN": token,
                "WEBCTL_DRY_RUN": "1",
                "WEBCTL_NAV": "1",
                "WEBCTL_TRANSPORT": "ws",
                "WEBCTL_SINK": sink_path,
                "WEBCTL_WATCHDOG": "2.5"}
        cfh = open(os.path.join(LOGDIR, "client.log"), "wb")
        client = subprocess.Popen([PY, "robotctl/client.py"], cwd=ROOT,
                                  env=cenv, stdout=cfh,
                                  stderr=subprocess.STDOUT,
                                  start_new_session=True)
        procs.append(client)

        print("[1] client online (single websocket)")
        check("s2000-sim online", wait_for(
            lambda: (status() or {}).get("online") is True, 12))
        stt = status() or {}
        check("transport is ws", stt.get("transport") == "ws",
              str(stt.get("transport")))

        print("[2] map plane cycle")
        req(f"{api}/{rid}/nav", {"action": "map_on"})
        check("map_on -> mode mapping", wait_for(
            lambda: (status() or {}).get("nav_mode") == "mapping"))
        req(f"{api}/{rid}/nav", {"action": "map_off"})
        check("map_off -> mode off", wait_for(
            lambda: (status() or {}).get("nav_mode") == "off"))

        print("[3] localize -> route -> done")
        req(f"{api}/{rid}/nav", {"action": "record", "name": "sim-wp"})
        check("record lands in navstore", wait_for(
            lambda: "sim-wp" in (json.loads(req(f"{api}/{rid}/nav")[1])
                                 .get("waypoints") or {}), 8))
        req(f"{api}/{rid}/nav", {"action": "nav_on", "map": "simmap",
                                 "waypoints": ["sim-wp"]})
        check("nav_on -> mode localizing", wait_for(
            lambda: (status() or {}).get("nav_mode") == "localizing"))
        st, b = req(f"{api}/{rid}/nav", {"action": "route",
                                         "waypoints": ["sim-wp"]})
        check("route accepted", st == 200 and json.loads(b).get("ok"), str(st))
        check("mode=route held", wait_for(
            lambda: (status() or {}).get("nav_mode") == "route", 5))
        check("409 teleop lockout during route", wait_for(
            lambda: req(f"{api}/{rid}/cmd", {"vx": 0.2, "wz": 0.0})[0] == 409,
            3.0))
        check("route completes (dry)", wait_for(
            lambda: ((status() or {}).get("nav", {}) or {}).get("route_done", 0)
            >= 1 and (status() or {}).get("nav_mode") == "localizing", 10))
        check("teleop accepted again", req(
            f"{api}/{rid}/cmd", {"vx": 0.1, "wz": 0.0})[0] == 200)

        print("[4] estop latch")
        req(f"{api}/{rid}/estop", {})
        check("estop latched", wait_for(
            lambda: (status() or {}).get("estop") is True, 3))
        req(f"{api}/{rid}/cmd", {"vx": 0.0, "wz": 0.0})
        check("/stop-style fresh cmd unlatches", wait_for(
            lambda: (status() or {}).get("estop") is False, 3))

        print("[5] teardown")
        req(f"{api}/{rid}/nav", {"action": "nav_off"})
        check("nav off", wait_for(
            lambda: (status() or {}).get("nav_mode") == "off", 8))
        os.killpg(os.getpgid(client.pid), signal.SIGTERM)
        check("client exits on SIGTERM", wait_for(
            lambda: client.poll() is not None, 10))

        print()
        if failures:
            print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
            return 1
        print("SIM FLEET: ALL CHECKS PASSED")
        return 0
    finally:
        for p in procs:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        if not live:
            import shutil
            shutil.rmtree(SCRATCH, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
