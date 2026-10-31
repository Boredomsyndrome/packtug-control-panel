#!/usr/bin/env python3
"""Fake rosbridge_server for OFFLINE testing of wheeltec-webctl.

Emulates just enough of the rosbridge v2 protocol:
  * accepts subscribe ops, replies with a fake battery Float32 at 2 Hz,
  * records every published Twist to memory + a log line,
  * serves a tiny HTTP endpoint on --http-port that prints the last cmd
    (handy for automated watchdog tests).

Usage:
    python3 fake_rosbridge.py --port 9091 --http-port 9092
"""
import argparse
import asyncio
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import websockets

LAST = {"vx": 0.0, "wz": 0.0, "t": 0.0, "n": 0}
LOG = []


class HTTP(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"last_cmd": LAST, "recent": LOG[-15:]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


async def handler(ws):
    async for raw in ws:
        try:
            m = json.loads(raw)
        except ValueError:
            continue
        op = m.get("op")
        if op == "subscribe":
            print(f"[fake] subscribe {m.get('topic')}")
        elif op == "publish" and m.get("topic") in ("/cmd_vel", "cmd_vel"):
            msg = m.get("msg", {})
            vx = msg.get("linear", {}).get("x", 0.0)
            wz = msg.get("angular", {}).get("z", 0.0)
            LAST.update(vx=vx, wz=wz, t=time.time(), n=LAST["n"] + 1)
            LOG.append({"t": round(time.time(), 3), "vx": vx, "wz": wz})


async def battery_broadcaster(ws):
    while True:
        await asyncio.sleep(0.5)
        for topic in ("/PowerVoltage",):
            await ws.send(json.dumps({"op": "publish", "topic": topic,
                                      "msg": {"data": 24.3}}))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9091)
    ap.add_argument("--http-port", type=int, default=9092)
    args = ap.parse_args()

    Thread(target=lambda: HTTPServer(("127.0.0.1", args.http_port), HTTP).serve_forever(),
           daemon=True).start()

    async def conn(ws):
        bcast = asyncio.create_task(battery_broadcaster(ws))
        try:
            await handler(ws)
        finally:
            bcast.cancel()

    print(f"[fake] rosbridge ws on :{args.port}, status http on :{args.http_port}")
    async with websockets.serve(conn, "127.0.0.1", args.port):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
