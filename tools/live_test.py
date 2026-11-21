#!/usr/bin/env python3
"""v3.4 live ladder — end-to-end autonomy smoke test against a REAL robot.

Run phase by phase:  python3 tools/live_test.py <phase> [base_url]
Phases: pre map_on drive save localize route halt cleanup
Motion is GATED by the caller choosing to run 'drive'/'route'/'halt'.
Aborts instantly on estop or low battery. State: tools/live_test.state.json
"""
import json, os, sys, time, urllib.request

BASE = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8090"
RID = "s2000-1"
API = f"{BASE}/api/robot/{RID}"
STATE_F = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_test.state.json")
MIN_BATT = 22.5


def _req(url, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def fleet():
    return _req(f"{BASE}/api/robot")[1]["robots"][0]


def nav():
    return _req(f"{API}/nav")[1]


def guard():
    f = fleet()
    if not f["online"]:
        die("robot OFFLINE")
    if f.get("estop"):
        die("E-STOP latched — aborting ladder")
    v = f.get("battery_v") or 0
    if v < MIN_BATT:
        die(f"battery {v:.1f}V < {MIN_BATT}V floor")
    return f


def die(msg):
    print(f"ABORT: {msg}")
    sys.exit(2)


def poll(cond, timeout, desc, every=2.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = nav()
        if cond(d):
            print(f"  ✓ {desc} ({time.time()-t0:.0f}s)")
            return d
        time.sleep(every)
    print(f"  ✗ TIMEOUT waiting for {desc} ({timeout}s). last={json.dumps(d.get('nav') or d)[:200]}")
    sys.exit(3)


def teleop(vx, wz, secs):
    """Heartbeat a velocity for secs, then hard stop. Caller owns risk."""
    print(f"  teleop vx={vx} wz={wz} for {secs:.1f}s ...")
    t0 = time.time()
    while time.time() - t0 < secs:
        _req(f"{API}/cmd", {"vx": vx, "wz": wz})
        time.sleep(0.1)
    _req(f"{API}/stop", {})
    time.sleep(1.0)
    f = fleet()
    print(f"    applied after stop: {f['applied']} | batt {f.get('battery_v'):.1f}V")


def save_state(d):
    s = {}
    if os.path.exists(STATE_F):
        s = json.load(open(STATE_F))
    s.update(d)
    json.dump(s, open(STATE_F, "w"))


def load_state():
    return json.load(open(STATE_F)) if os.path.exists(STATE_F) else {}


def rec(name):
    st, d = _req(f"{API}/nav", {"action": "record", "name": name})
    if st != 200 or not d.get("ok"):
        die(f"record {name} rejected: {st} {d}")
    poll(lambda x: x.get("waypoints", {}).get(name), 15, f"waypoint {name} stored")


def phase_pre():
    st, d = _req(f"{API}/nav", {"action": "route"})          # missing waypoints → 400
    print("bad-payload validation:", st, "(want 400)" if st == 400 else "WRONG")
    st2, d2 = _req(f"{BASE}/api/robot/nosuchrobot/nav", {"action": "map_on"})
    print("unknown robot:", st2, "(want 404)" if st2 == 404 else "WRONG")
    f = guard()
    n = nav()
    print(f"robot online batt={f['battery_v']:.1f}V scan={f['scan']} mode={n.get('mode')}")
    if n.get("mode") != "off":
        print("note: nav not off — resetting")
        _req(f"{API}/nav", {"action": "nav_cancel"}); _req(f"{API}/nav", {"action": "nav_off"})
        time.sleep(5)
    print("PRE OK")


def phase_map_on():
    guard()
    st, d = _req(f"{API}/nav", {"action": "map_on"})
    print("map_on queued:", st, d)
    poll(lambda x: x.get("mode") == "mapping", 90, "mode=mapping")
    poll(lambda x: isinstance((x.get("nav") or {}).get("pose"), list)
         and len(x["nav"]["pose"]) >= 2, 60, "slam pose available")
    print("MAP_ON OK — slam_toolbox running, robot NOT moving")


def phase_drive():
    guard()
    n = nav()
    if n.get("mode") != "mapping":
        die("not mapping; run map_on first")
    p0 = (n.get("nav") or {}).get("pose")
    teleop(0.05, 0.0, 1.2)                       # gentle nudge to prove motion path
    n = nav(); p1 = (n.get("nav") or {}).get("pose")
    moved = None
    if p0 and p1:
        moved = ((p1[0]-p0[0])**2 + (p1[1]-p0[1])**2) ** 0.5
        print(f"  map-frame motion: {moved*100:.1f} cm  ({'OK' if moved > 0.02 else 'no motion!'} )")
    if not moved or moved <= 0.02:
        die("nudge produced no map-frame motion — SLAM/odometry issue")
    rec("p1")
    teleop(0.10, 0.0, 3.0)                       # forward leg
    teleop(0.0, 0.35, 2.0)                       # rotate ~40°
    rec("p2")
    teleop(0.10, 0.0, 2.5)                       # second leg
    teleop(0.0, -0.35, 2.0)
    rec("p3")
    n = nav()
    print("waypoints:", {k: [round(v['x'],2), round(v['y'],2)] for k, v in n["waypoints"].items()})
    save_state({"drove": True})
    print("DRIVE OK — triangle driven, p1/p2/p3 recorded")


def phase_save():
    guard()
    st, d = _req(f"{API}/nav", {"action": "map_save"})
    print("map_save queued:", st, d)
    poll(lambda x: x.get("maps"), 90, "map artifact on hub")
    n = nav()
    print("maps:", n["maps"])
    print("SAVE OK")


def phase_localize():
    guard()
    st, d = _req(f"{API}/nav", {"action": "map_off"})
    print("map_off:", st)
    time.sleep(4)
    m = nav()["maps"][-1]["name"] if nav().get("maps") else "map"
    st, d = _req(f"{API}/nav", {"action": "nav_on", "map": m})
    print(f"nav_on map={m}:", st, d)
    poll(lambda x: x.get("mode") in ("localizing", "nav"), 120, "mode=localizing")
    poll(lambda x: isinstance((x.get("nav") or {}).get("pose"), list), 60, "amcl pose")
    print("LOCALIZE OK — Nav2 up, still NOT moving")


def phase_route():
    guard()
    wps = load_state().get("route", ["p1", "p2", "p3"])
    st, d = _req(f"{API}/nav", {"action": "route", "waypoints": wps})
    print("route queued:", st, d, wps)
    poll(lambda x: x.get("mode") == "route", 30, "mode=route (MOVING)")
    last = -1
    t0 = time.time()
    while time.time() - t0 < 300:
        n = nav()
        stt = n.get("nav") or {}
        wi = stt.get("wp_index", -1)
        if wi != last:
            print(f"  wp_index={wi}/{len(wps)} mode={n.get('mode')} pose={[round(v,2) for v in (stt.get('pose') or [])][:2]}")
            last = wi
        if n.get("mode") != "route":
            print(f"ROUTE FINISHED → mode={n.get('mode')} after {time.time()-t0:.0f}s")
            guard()
            print("ROUTE OK")
            return
        if not stt.get("driving", True) and time.time() - t0 > 20 and (wi or 0) == 0:
            die("route not driving but mode stuck — inspect nav.error: " + str(stt.get("error")))
        time.sleep(3)
    _req(f"{API}/nav", {"action": "nav_cancel"})
    die("route TIMEOUT 300s — cancelled for safety")


def phase_halt():
    guard()
    st, d = _req(f"{API}/nav", {"action": "route", "waypoints": ["p3", "p2", "p1"]})
    print("return route queued:", st)
    poll(lambda x: x.get("mode") == "route", 30, "mode=route")
    time.sleep(6)
    st, d = _req(f"{API}/nav", {"action": "nav_cancel"})
    print("nav_cancel:", st)
    poll(lambda x: x.get("mode") != "route", 30, "route cancelled")
    time.sleep(2)
    f = fleet()
    print("applied after halt:", f["applied"])
    if any(abs(v) > 0.01 for v in (f["applied"] or {}).values()):
        die("robot still commanding velocity after cancel!")
    print("HALT OK")


def phase_cleanup():
    n = nav()
    for w in (n.get("waypoints") or {}):
        _req(f"{API}/nav", {"action": "delete_waypoint", "waypoints": [w]})
    for m in (n.get("maps") or []):
        _req(f"{API}/nav", {"action": "delete_map", "map": m["name"]})
    if (n.get("mode") or "off") != "off":
        _req(f"{API}/nav", {"action": "nav_off"})
    time.sleep(5)
    n = nav()
    print("final:", {k: n.get(k) for k in ("mode", "maps")}, "wps:", list(n.get("waypoints", {})))
    print("CLEANUP OK" if not n.get("waypoints") and not n.get("maps") else "leftovers remain")


if __name__ == "__main__":
    ph = sys.argv[1]
    print(f"== live_test phase: {ph} @ {BASE} ==")
    {"pre": phase_pre, "map_on": phase_map_on, "drive": phase_drive,
     "save": phase_save, "localize": phase_localize, "route": phase_route,
     "halt": phase_halt, "cleanup": phase_cleanup}[ph]()
