"""The hub API — FastAPI port of the former Django/DRF robot plane (v3.8).

Route-for-route identical to the old urls.py + viewsets.py + robotctl.py +
views.py; response bodies and error shapes ({ok:false, error, errors}) are
byte-compatible so neither the robot client nor the SPAs need changes.

    BROWSER plane (network trust, no auth):
      GET  /api/robot                     fleet status
      GET  /api/robot/<id>                one robot
      POST /api/robot/<id>/cmd            velocity (validated + clamped)
      POST /api/robot/<id>/stop           stop + clear latched e-stop
      POST /api/robot/<id>/estop          latched e-stop (+ nav cancel)
      GET  /api/robot/<id>/scan           newest LiDAR ring
      GET  /api/robot/<id>/nav            nav state + waypoints + maps
      POST /api/robot/<id>/nav            nav action (docs/NAV.md)
      GET  /api/robot/<id>/maps/<f>       saved map artifact (.pgm/.yaml)
      GET  /cam/<id>                      MJPEG relay of the newest frame
      /                                  panel SPA · /agv → console SPA

    ROBOT plane (per-robot Basic-auth; robot is a PURE CLIENT):
      GET  /robotctl/<id>/cmd             long-poll effective command
      POST /robotctl/<id>/telemetry       {battery, applied, nav, odom...}
      POST /robotctl/<id>/frame           JPEG relay body
      POST /robotctl/<id>/scan            packed LaserScan (HTTP fallback)
      POST /robotctl/<id>/map             saved-map upload (HTTP fallback)

The WebSockets (/ws/robotctl/<id>, /ws/cam/<id>) live in robot/ws.py as
raw ASGI handlers — mounted by webctl/asgi.py, framework-neutral.
"""
import asyncio
import base64
import json
import logging
import mimetypes
import os
import time
import zlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import (FileResponse, JSONResponse, RedirectResponse,
                               StreamingResponse)

from webctl import config
from robot import navstore
from robot.hub import SCAN_FRESH, registry
from robot.validators import (NavActionBody, ScanPayload, TelemetryCommand,
                              VelocityCommand, parse)

log = logging.getLogger("robot.api")

NOT_OWNER = ("this process does not own the robot hub state "
             "(run exactly one server process)")

router = APIRouter()


def _err(status, msg, headers=None):
    return JSONResponse({"ok": False, "error": msg}, status_code=status,
                        headers=headers or {})


def _hub_or_404(robot_id):
    hub = registry.robot(robot_id)
    if hub is None:
        return None, _err(404, f"unknown robot '{robot_id}'")
    return hub, None


# ===================== browser plane =====================

@router.get("/api/robot")
def robot_list():
    """GET /api/robot — status of every registered robot."""
    return {"ok": True, "robots": registry.summary(),
            "camera_path": "/cam/<id>"}


@router.get("/api/robot/{robot_id}")
async def robot_retrieve(robot_id: str):
    hub, err = _hub_or_404(robot_id)
    if err:
        return err
    data = hub.status()
    data["ok"] = True
    data["camera_path"] = f"/cam/{hub.robot_id}"
    return data


@router.post("/api/robot/{robot_id}/cmd")
async def robot_cmd(robot_id: str, request: Request):
    hub, err = _hub_or_404(robot_id)
    if err:
        return err
    if not registry.owns:
        return _err(503, NOT_OWNER)
    if hub.nav_mode == "route":
        # UX layer only — the ROBOT also gates its teleop publisher while
        # Nav2 drives; refuse here so buttons can't fight the route.
        return _err(409, "robot is driving a route autonomously — "
                         "cancel the route before teleoperating")
    body, perr = parse(VelocityCommand, await _json_or_empty(request))
    if perr:
        return JSONResponse({"ok": False, "error": "invalid command",
                             "errors": perr}, status_code=400)
    vx, wz = body["vx"], body["wz"]
    clamped = hub.set_cmd(vx, wz)
    return {"ok": True, "vx": clamped[0], "wz": clamped[1],
            "clamped": clamped != (vx, wz)}


@router.get("/api/robot/{robot_id}/scan")
async def robot_scan(robot_id: str):
    """Newest LiDAR ring (packed LaserScan). Browser polls ~2-5 Hz;
    0.0 ranges mean 'no return' (gap)."""
    hub, err = _hub_or_404(robot_id)
    if err:
        return err
    scan, ts = hub.scan_now()
    now = time.time()
    return {"ok": True,
            "age": (now - ts) if ts else None,
            "fresh": bool(scan) and (now - ts) < SCAN_FRESH,
            "scan": scan}


@router.get("/api/robot/{robot_id}/nav")
async def nav_get(robot_id: str):
    hub, err = _hub_or_404(robot_id)
    if err:
        return err
    maps = navstore.merge_maps(
        navstore.list_maps(robot_id),
        (hub.nav_state or {}).get("local_maps"))
    return {"ok": True, "mode": hub.nav_mode, "nav": hub.nav_state,
            "waypoints": navstore.list_waypoints(robot_id), "maps": maps}


@router.post("/api/robot/{robot_id}/nav")
async def nav_post(robot_id: str, request: Request):
    """Validated nav action queued into the hub mailbox. Actions reach the
    robot through the SAME cmd plane; only 'route' can produce motion, and
    only from stored waypoints."""
    hub, err = _hub_or_404(robot_id)
    if err:
        return err
    if not registry.owns:
        return _err(503, NOT_OWNER)
    body, perr = parse(NavActionBody, await _json_or_empty(request))
    if perr:
        return JSONResponse({"ok": False, "error": "invalid nav action",
                             "errors": perr}, status_code=400)
    a = body["action"]
    name = body.get("name")
    map_name = body.get("map")
    names = body.get("waypoints") or []

    if hub.nav_mode == "route" and a not in ("nav_cancel", "nav_off"):
        return _err(409, "route running — only nav_cancel / nav_off accepted")

    if a == "route":
        wps = navstore.list_waypoints(robot_id)
        missing = [w for w in names if w not in wps]
        if missing:
            return JSONResponse({"ok": False, "error": "unknown waypoints",
                                 "missing": missing}, status_code=400)
        poses = [[wps[w]["x"], wps[w]["y"], wps[w]["yaw"]] for w in names]
        # pass the last-known map so the robot can AUTO-ARM the stack if
        # it is sitting idle (route button = "whatever it takes to drive")
        rid = hub.push_nav("route", poses=poses, order=list(names),
                           map=(hub.nav_state or {}).get("map"))
    elif a == "align":
        wps = navstore.list_waypoints(robot_id)
        wp = wps.get(names[0])
        if wp is None:
            return _err(400, "unknown waypoint")
        rid = hub.push_nav("align", align_pose=[wp["x"], wp["y"], wp["yaw"]])
    elif a == "record":
        name = name or time.strftime("wp-%H%M%S")
        rid = hub.push_nav("record", name=name)
        if rid is not None:
            hub.expect_waypoint(rid, name)   # telemetry lands as this name
    elif a in ("delete_map", "delete_waypoint"):
        if a == "delete_map":
            gone = navstore.delete_map(robot_id, map_name)
            hub.push_nav("delete_map", map=map_name)  # robot copy too
        else:
            gone = navstore.delete_waypoint(robot_id, names[0])
        if not gone:
            return _err(404, "nothing deleted")
        rid = None
    elif a == "clear_waypoints":
        # hub-local only: waypoints live in the hub store and routes are
        # built from it, so nothing needs to reach the robot.
        if not navstore.clear_waypoints(robot_id):
            return _err(404, "nothing deleted")
        rid = None
    elif a == "map_save":
        rid = hub.push_nav("map_save", name=name or "room1")
    elif a == "nav_on":
        extra = {}
        if names:                      # optional align hint: start
            wps = navstore.list_waypoints(robot_id)      # localization armed
            wp = wps.get(names[0])                       # at this waypoint
            if wp is None:
                return _err(400, "unknown waypoint")
            extra["align_pose"] = [wp["x"], wp["y"], wp["yaw"]]
        rid = hub.push_nav("nav_on", map=map_name, **extra)
    else:                                  # map_on|map_off|nav_cancel|nav_off
        rid = hub.push_nav(a)
    if rid is None and a not in ("delete_map", "delete_waypoint",
                                 "clear_waypoints"):
        return _err(409, "robot offline — action NOT queued "
                         "(it will not fire on reconnect)")
    return {"ok": True, "action": a, "rid": rid}


@router.post("/api/robot/{robot_id}/stop")
async def robot_stop(robot_id: str):
    """Zero velocity AND clear latched e-stop."""
    hub, err = _hub_or_404(robot_id)
    if err:
        return err
    if not registry.owns:
        return _err(503, NOT_OWNER)
    hub.stop()
    return {"ok": True}


@router.post("/api/robot/{robot_id}/estop")
async def robot_estop(robot_id: str):
    """Latch zero until an explicit new command."""
    hub, err = _hub_or_404(robot_id)
    if err:
        return err
    if not registry.owns:
        return _err(503, NOT_OWNER)
    hub.emergency_stop()
    return {"ok": True, "estop": True}


@router.get("/api/robot/{robot_id}/maps/{fname}")
async def map_file_view(robot_id: str, fname: str):
    """Serve a saved map artifact to the browser (.pgm / .yaml).
    navstore regex-locks name+ext so the path can never escape the maps dir."""
    if registry.robot(robot_id) is None:
        return _err(404, "unknown robot")
    name, _, ext = fname.rpartition(".")
    if not name or ext not in ("pgm", "yaml"):
        return _err(404, "map file not found")
    path = navstore.map_file(robot_id, name, ext)
    if path is None:
        return _err(404, "map file not found")
    ctype = ("image/x-portable-graymap" if ext == "pgm" else "text/yaml")
    return FileResponse(path, media_type=ctype, filename=f"{name}.{ext}")


@router.get("/cam/{robot_id}")
async def cam_view(robot_id: str):
    """ASGI-native MJPEG relay (frames arrive via the robot's websocket).
    StreamingResponse + async generator: true streaming under uvicorn."""
    hub = registry.robot(robot_id)
    if hub is None:
        return _err(404, "unknown robot")
    if hub._loop is None:                # adopt the running loop so hub
        hub.set_event_loop(asyncio.get_running_loop())   # wakeups reach us

    ev = asyncio.Event()
    remove = hub.add_frame_listener(ev)
    frame, seen = hub.frame_now()
    if frame is None:
        try:          # first frame may take a moment after the robot starts
            await asyncio.wait_for(ev.wait(), 2.0)
        except (TimeoutError, asyncio.TimeoutError):
            remove()
            return _err(503, "no camera frames from this robot (yet) — "
                             "is its client running with a camera enabled?")
        ev.clear()
        frame, seen = hub.frame_now()
        if frame is None:
            remove()
            return _err(503, "no camera frames")

    async def gen():
        try:
            f, s = frame, seen
            while True:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n"
                       b"Content-Length: " + str(len(f)).encode() + b"\r\n\r\n")
                yield f
                yield b"\r\n"
                try:
                    await asyncio.wait_for(ev.wait(), 5.0)
                except (TimeoutError, asyncio.TimeoutError):
                    pass                     # stale link: re-send last frame
                ev.clear()
                cur, seq = hub.frame_now()
                if cur is not None and seq > s:
                    f, s = cur, seq
        finally:
            remove()

    return StreamingResponse(
        gen(), media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-transform",
                 "X-Accel-Buffering": "no"})   # no proxy buffering (nginx/Caddy)


@router.get("/api/schema", include_in_schema=False)
async def schema_redirect():
    return RedirectResponse("/docs")


@router.get("/api/redoc", include_in_schema=False)
async def redoc_redirect():
    return RedirectResponse("/docs")


# ===================== robot plane (Basic auth) =====================

def _authenticate(request: Request, robot_id: str):
    """Return (hub, error_response). Unknown id -> 404, bad creds -> 401."""
    hub = registry.robot(robot_id)
    if hub is None:
        return None, _err(404, "unknown robot")
    if not registry.owns:
        return None, _err(503, NOT_OWNER)
    username, token = "", ""
    header = request.headers.get("authorization", "")
    if header.startswith("Basic "):
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
            username, _, token = decoded.partition(":")
        except (ValueError, UnicodeDecodeError):
            pass
    if not registry.check_token(robot_id, username, token):
        return None, _err(401, "bad robot credentials",
                          headers={"WWW-Authenticate":
                                   'Basic realm="wheeltec-robotctl"'})
    return hub, None


async def _json_or_empty(request: Request):
    body = await request.body()
    try:
        return json.loads(body or b"{}")
    except ValueError:
        return None           # callers report their own 400 text


@router.get("/robotctl/{robot_id}/cmd")
def cmd_poll(robot_id: str, request: Request, seq: str = None, wait: str = "0.4"):
    """Long-poll (SYNC on purpose: blocks a threadpool worker, mirroring the
    old Django view). Query: seq (last applied generation, omit on first
    call), wait (0..1 s, default 0.4). Always returns the *effective*
    command: zero if latched e-stop or the held command went stale."""
    hub, err = _authenticate(request, robot_id)
    if err:
        return err
    try:
        seq_i = int(seq) if seq not in (None, "") else None
        wait_f = float(wait)
    except ValueError:
        return _err(400, "seq must be an integer, wait a number")
    if seq_i is not None and seq_i < 0:
        return _err(400, "seq must be >= 0")
    return {"ok": True, **hub.poll(seq_i, wait_f)}


@router.post("/robotctl/{robot_id}/telemetry")
async def telemetry(robot_id: str, request: Request):
    """Telemetry uplink from the robot client (JSON, <= 8 KB)."""
    hub, err = _authenticate(request, robot_id)
    if err:
        return err
    body = await request.body()
    if len(body) > 8192:
        return _err(413, "telemetry too large")
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return _err(400, "telemetry must be valid JSON")
    if not isinstance(data, dict):
        return _err(400, "telemetry must be a JSON object")
    obj, _errs = parse(TelemetryCommand, data)
    if obj is None:
        return _err(400, f"invalid telemetry: {_errs}")
    hub.set_telemetry(TelemetryCommand.model_validate(data).to_hub())
    return {"ok": True}


@router.post("/robotctl/{robot_id}/frame")
async def frame(robot_id: str, request: Request):
    """One camera JPEG per request (HTTP fallback transport; the ws link
    carries frames as binary messages)."""
    hub, err = _authenticate(request, robot_id)
    if err:
        return err
    length = request.headers.get("content-length")
    if length and int(length) > config.MAX_FRAME:
        return _err(413, f"frame exceeds MAX_FRAME ({config.MAX_FRAME} B)")
    jpeg = await request.body()
    if not jpeg.startswith(b"\xff\xd8"):
        return _err(400, "body is not a JPEG")
    hub.set_frame(jpeg)
    return {"ok": True}


def save_map_payload(robot_id, data):
    """Validate + persist one map-upload payload (JSON dict, pgm gz/b64).

    SHARED by the HTTP /robotctl/<id>/map fallback endpoint and the v3.7
    ws {"type":"map"} plane (single-websocket policy). Raises
    ValueError/OSError on rejection; returns the stored map list."""
    if not isinstance(data, dict):
        raise ValueError("map upload must be a JSON object")
    if data.get("enc") == "gz":      # v3.5: zlib'd pgm (flaky-link safe)
        blob = base64.b64decode(data.get("pgm_gz_b64") or "", validate=True)
        dobj = zlib.decompressobj()
        pgm = dobj.decompress(blob, navstore.MAX_PGM + 1)
        pgm += dobj.flush()
        if len(pgm) > navstore.MAX_PGM:
            raise ValueError(f"decompressed pgm exceeds {navstore.MAX_PGM} B")
    else:
        pgm = base64.b64decode(data.get("pgm_b64") or "", validate=True)
    navstore.save_map(robot_id, str(data.get("name") or ""), pgm,
                      str(data.get("yaml") or ""))
    return navstore.list_maps(robot_id)


@router.post("/robotctl/{robot_id}/map")
async def map_upload(robot_id: str, request: Request):
    """v3.3: the robot uploads a saved map (navctl map-save output).
    v3.7: only reached in HTTP-fallback mode — with a healthy ws the client
    ships the same payload as a ws 'map' frame (robot/ws.py reader)."""
    hub, err = _authenticate(request, robot_id)
    if err:
        return err
    body = await request.body()
    if len(body) > 12 * 1024 * 1024:
        return _err(413, "map upload too large")
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return _err(400, "map upload must be valid JSON")
    try:
        maps = await asyncio.to_thread(save_map_payload, robot_id, data)
    except (ValueError, OSError, TypeError, zlib.error) as e:
        return _err(400, f"map rejected: {e}")
    return {"ok": True, "maps": maps}


@router.post("/robotctl/{robot_id}/scan")
async def scan_uplink(robot_id: str, request: Request):
    """One packed LaserScan per request (HTTP fallback transport; the ws
    link carries the same JSON as a {"type":"scan"} text frame)."""
    hub, err = _authenticate(request, robot_id)
    if err:
        return err
    body = await request.body()
    if len(body) > 65536:
        return _err(413, "scan too large")
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return _err(400, "scan must be valid JSON")
    if not isinstance(data, dict):
        return _err(400, "scan must be a JSON object")
    obj, errs = parse(ScanPayload, data)
    if obj is None:
        return _err(400, f"invalid scan: {errs}")
    hub.set_scan(obj)
    return {"ok": True}


# ===================== SPA serving (was WhiteNoise + index_view) ==========

DIST = Path(config.PROJECT_ROOT) / "dist"


def _file_or_none(path: Path):
    """Return the real file under DIST or None (no symlink escape)."""
    try:
        real = path.resolve()
        real.relative_to(DIST.resolve())
    except (ValueError, OSError):
        return None
    if real.is_file():
        return real
    return None


@router.api_route("/{full_path:path}", methods=["GET", "HEAD"],
                  include_in_schema=False)
async def spa(full_path: str, request: Request):
    """Static assets, then the two SPA indexes (no-cache on HTML — a stale
    index pins hashed bundles and shows a BLANK PAGE)."""
    path = full_path.strip("/")
    apiish = path.startswith(("api/", "cam/", "robotctl/", "ws/"))
    if apiish:
        return _err(404, "not found")

    if path:
        f = _file_or_none(DIST / path)
        if f is not None:
            ctype, encoding = mimetypes.guess_type(f.name)
            if f.suffix == ".js":
                ctype = "text/javascript"        # exact CS-B4 expectation
            elif f.suffix == ".css":
                ctype = "text/css"
            elif f.suffix == ".html":
                ctype = "text/html; charset=utf-8"
            headers = {}
            if path.startswith("assets/"):
                headers["Cache-Control"] = "public, max-age=604800, immutable"
            elif f.suffix in (".html", ".htm"):
                headers["Cache-Control"] = "no-cache, must-revalidate"
            return FileResponse(f, media_type=ctype or "application/octet-stream",
                                filename=None, headers=headers)

    agv = path == "agv" or path.startswith("agv/")
    index = DIST / "agv" / "index.html" if agv else DIST / "index.html"
    if not index.exists():
        hint = ("cd agv-console && npm ci && npm run build" if agv
                else "npm run build")
        return StreamingResponse(
            iter([f"<h1>PackTug hub</h1><p>Frontend not built yet. Run "
                  f"<code>{hint}</code>.</p>"]),
            media_type="text/html", status_code=200)
    return FileResponse(index, media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-cache, must-revalidate"})


# ===================== app factory =====================

@asynccontextmanager
async def _lifespan(app: FastAPI):
    owned = registry.acquire_ownership()
    loop = asyncio.get_running_loop()
    for hub in registry.hubs.values():
        hub.set_event_loop(loop)
    log.info("webctl (FastAPI) up; hub owner=%s robots=%s",
             owned, ",".join(registry.hubs))
    yield


def build_app() -> FastAPI:
    app = FastAPI(
        title="WHEELTEC WebCtl API",
        description=("Hub API: robot fleet, velocity/estop, LiDAR frames and "
                     "the nav remote (docs/NAV.md). Browser plane is "
                     "network-trusted (LAN/VPN); robot plane keeps per-robot "
                     "tokens. (The arm/vision bridge publishes its own "
                     "schema at /api/openapi.json.)"),
        version=os.environ.get("PACKTUG_VERSION", "3.8.0"),
        lifespan=_lifespan,
    )
    app.include_router(router)
    return app
