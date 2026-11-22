"""WebSocket endpoints (raw ASGI — routed by webctl/asgi.py, no Channels).

Robot uplink (the robot is still a PURE CLIENT: it dials the server, the
server never dials it):

    ws://<server>/ws/robotctl/<id>?token=<per-robot token>

    server -> robot : {"type":"welcome"|"cmd", vx, wz, estop, seq, ...}
                      {"type":"takeover"}   (a newer client connection won)
    robot  -> server: {"type":"hello"|"telemetry", battery_v, applied_vx/wz,
                       watchdog_trips, ...}   and BINARY messages = JPEG frames

Browser viewer:

    ws://<server>/ws/cam/<id>     server pushes BINARY = 8-byte float64
                                  little-endian server timestamp + JPEG.
                                  Drop-latest semantics: viewers only ever
                                  get the newest frame.

Why WS for video: one POST per frame costs a request round-trip, headers and
per-request buffering (~150-400 ms seen on congested WiFi); a single socket
pushes 15 fps at tens of milliseconds. Commands move the same way instantly.

Safety is transport-independent: cmd snapshots come from RobotHub.effective()
(same zero-safe state machine as the HTTP long-poll), robot auth mirrors
robotctl._authenticate rules, frames are size-capped like the HTTP route.
"""
import asyncio
import json
import logging
import struct
import time
import zlib
from urllib.parse import parse_qs

from webctl import config
from robot.hub import registry

log = logging.getLogger("robot.ws")

TAKEOVER = 4409


def _qs(scope):
    return parse_qs(scope.get("query_string", b"").decode("latin-1"))


async def _send_json(ws_send, obj):
    await ws_send({"type": "websocket.send", "text": json.dumps(obj)})


async def _deny(send, code):
    try:
        await send({"type": "websocket.close", "code": code})
    except Exception:
        pass


def _robot_resolve(scope, robot_id):
    """(hub, close_code): mirrors robotctl._authenticate for a WS handshake."""
    hub = registry.robot(robot_id)
    if hub is None:
        return None, 4404
    if not registry.owns:
        return None, 4503
    q = _qs(scope)
    token = (q.get("token") or [""])[0]
    username = (q.get("u") or [robot_id])[0]
    if not registry.check_token(robot_id, username, token):
        return None, 4401
    return hub, 0


async def robot_ws(scope, receive, send, robot_id, **_kw):
    hub, code = _robot_resolve(scope, robot_id)
    if hub is None:
        await _deny(send, code)
        return
    await send({"type": "websocket.accept"})
    epoch = hub.take_ws_slot()
    hub.set_ws_connected(True, epoch)
    log.info("robot '%s' ws connected (epoch %d)", hub.robot_id, epoch)

    cmd_ev = asyncio.Event()
    remove = hub.add_cmd_listener(cmd_ev)
    reader_q = asyncio.Queue()

    async def pump():
        """Push the effective cmd whenever its generation changes (or every
        0.25 s tick, so held-cmd expiry to zero goes out even un-signalled)."""
        sent_key = None
        while True:
            try:
                await asyncio.wait_for(cmd_ev.wait(), timeout=0.25)
            except (asyncio.TimeoutError, TimeoutError):
                pass
            cmd_ev.clear()
            if hub.ws_epoch != epoch:
                await _send_json(send, {"type": "takeover"})
                return
            snap = hub.effective()
            key = (snap["seq"], snap["estop"], snap["vx"], snap["wz"])
            if key != sent_key:
                await _send_json(send, {"type": "cmd", **snap})
                sent_key = key

    async def reader():
        from robot.validators import (ScanPayload, TelemetryCommand,
                                     parse)
        while True:
            msg = await receive()
            mtype = msg["type"]
            if mtype == "websocket.disconnect":
                await reader_q.put(("disc", None))
                return
            data = msg.get("bytes")
            if data is not None:
                if len(data) <= config.MAX_FRAME and data.startswith(b"\xff\xd8"):
                    hub.set_frame(bytes(data))
                hub.contact("ws")
                continue
            txt = msg.get("text")
            if not txt:
                continue
            try:
                obj = json.loads(txt)
            except ValueError:
                continue
            kind = obj.get("type")
            hub.contact("ws")
            if kind == "telemetry":
                tel, errs = parse(TelemetryCommand, obj)
                if tel is not None:
                    hub.set_telemetry(TelemetryCommand.model_validate(obj).to_hub(),
                                      transport="ws")
                else:
                    log.debug("telemetry rejected: %s", errs)
            elif kind == "scan":
                scan, errs = parse(ScanPayload, obj)
                if scan is not None:
                    hub.set_scan(scan)
                else:
                    log.debug("scan rejected: %s", errs)
            elif kind == "map":
                # v3.7 single-websocket policy: map uploads ride the ws
                # (the HTTP /robotctl/<id>/map endpoint only answers in
                # degraded fallback mode). Same validation + storage path
                # as the HTTP plane via save_map_payload.
                if len(txt) > 12 * 1024 * 1024:
                    await _send_json(send, {"type": "map_ack",
                                            "rid": obj.get("rid"),
                                            "ok": False,
                                            "detail": "map too large"})
                    continue
                from robot.api import save_map_payload
                try:
                    maps = await asyncio.to_thread(save_map_payload,
                                                   hub.robot_id, obj)
                    await _send_json(send, {"type": "map_ack",
                                            "rid": obj.get("rid"),
                                            "ok": True, "maps": maps})
                except (ValueError, OSError, TypeError, zlib.error) as e:
                    await _send_json(send, {"type": "map_ack",
                                            "rid": obj.get("rid"),
                                            "ok": False,
                                            "detail": str(e)[:200]})
            elif kind == "bye":
                await reader_q.put(("disc", None))
                return

    try:
        await _send_json(send, {"type": "welcome", **hub.effective()})
        pump_t = asyncio.create_task(pump())
        read_t = asyncio.create_task(reader())
        done, pending = await asyncio.wait(
            {pump_t, read_t}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for t in done:
            exc = t.exception()
            if exc:
                log.warning("robot '%s' ws ended: %r", hub.robot_id, exc)
    finally:
        remove()
        hub.set_ws_connected(False)
        log.info("robot '%s' ws disconnected (epoch %d)", hub.robot_id, epoch)


async def viewer_ws(scope, receive, send, robot_id, **_kw):
    """Browser camera viewer: binary = struct.pack('<d', server_ts) + jpeg."""
    hub = registry.robot(robot_id)
    if hub is None:
        await _deny(send, 4404)
        return
    origin = dict(scope.get("headers", [])).get(b"origin")
    if origin is not None:  # browsers always send Origin; python tools may not
        o_netloc = origin.decode().split("://")[-1].rstrip("/")
        host, port = scope["server"][0], scope["server"][1]
        o_host, _, o_port = o_netloc.partition(":")
        if (o_host != host
                or (o_port and int(o_port or 0) not in (port, 80, 443))):
            await _deny(send, 4403)
            return
    await send({"type": "websocket.accept"})
    frame_ev = asyncio.Event()
    remove = hub.add_frame_listener(frame_ev)
    closed = asyncio.Event()

    async def reader():
        while True:
            msg = await receive()
            if msg["type"] == "websocket.disconnect":
                closed.set()
                return

    async def pusher():
        _, seen = hub.frame_now()

        def pack(frame):
            return struct.pack("<d", time.time()) + frame

        cur, seen = hub.frame_now()          # start with the freshest image
        if cur:
            await send({"type": "websocket.send", "bytes": pack(cur)})
        while not closed.is_set():
            try:
                await asyncio.wait_for(frame_ev.wait(), timeout=2.0)
            except (asyncio.TimeoutError, TimeoutError):
                pass
            frame_ev.clear()
            cur, seq = hub.frame_now()
            if cur and seq > seen:
                seen = seq
                await send({"type": "websocket.send", "bytes": pack(cur)})

    try:
        r, p = asyncio.create_task(reader()), asyncio.create_task(pusher())
        done, pending = await asyncio.wait({r, p}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        remove()
