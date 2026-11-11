#!/usr/bin/env python3
"""Minimal ROS2 image -> MJPEG bridge for the PackTug control panel.

Runs on the robot Jetson alongside ``realsense2_camera``. It subscribes to the
color and depth ``sensor_msgs/Image`` topics and serves:

  /stream          color MJPEG
  /snapshot        color JPEG
  /depth           colorized depth MJPEG
  /depth_snapshot  colorized depth JPEG
  /depth_stats     nearest/farthest valid depth in metres
  /health          JSON status

Usage on the Jetson:

    source /opt/ros/humble/setup.bash
    python3 server/camera_stream.py --port 8080
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image

DEPTH_NEAR_M = 0.15
DEPTH_FAR_M = 1.5


class LatestJpeg:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._stamp = 0.0
        self._meta: dict[str, float | int | str | None] = {}

    def update(self, jpeg: bytes, meta: dict[str, float | int | str | None]) -> None:
        with self._lock:
            self._jpeg = jpeg
            self._stamp = time.time()
            self._meta = dict(meta)

    def get(self) -> bytes | None:
        with self._lock:
            return self._jpeg

    def age(self) -> float | None:
        with self._lock:
            return (time.time() - self._stamp) if self._stamp else None

    def meta(self) -> dict[str, float | int | str | None]:
        with self._lock:
            data = dict(self._meta)
            data["age_sec"] = (time.time() - self._stamp) if self._stamp else None
            return data

    def clear(self) -> None:
        with self._lock:
            self._jpeg = None
            self._stamp = 0.0
            self._meta = {}


COLOR = LatestJpeg()
DEPTH = LatestJpeg()
MANAGER: CameraProcess | None = None


class CameraProcess:
    """Own one realsense2_camera process at a time.

    Only one sensor is enabled per mode so the D455 can run over USB2 without
    competing color and depth streams.
    """

    def __init__(self, rgb_profile: str, depth_profile: str, log_path: str = "/tmp/realsense_mode.log") -> None:
        self.rgb_profile = rgb_profile
        self.depth_profile = depth_profile
        self.log_path = log_path
        self.mode: str | None = None
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def _command(self, mode: str) -> str:
        args = [
            "ros2",
            "launch",
            "realsense2_camera",
            "rs_launch.py",
            "camera_name:=d455",
            "depth_module.profile:=" + self.depth_profile,
            "rgb_camera.profile:=" + self.rgb_profile,
        ]
        if mode == "rgb":
            args += ["enable_color:=true", "enable_depth:=false"]
        else:
            args += ["enable_color:=false", "enable_depth:=true"]
        return "source /opt/ros/humble/setup.bash; exec " + " ".join(shlex.quote(a) for a in args)

    def start(self, mode: str) -> None:
        if mode not in ("rgb", "depth"):
            raise ValueError(f"invalid camera mode: {mode}")
        with self._lock:
            self.stop()
            COLOR.clear()
            DEPTH.clear()
            log = open(self.log_path, "ab")
            try:
                self._proc = subprocess.Popen(
                    ["bash", "-lc", self._command(mode)],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            finally:
                log.close()
            self.mode = mode

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        self.mode = None
        if proc is None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait(timeout=2)


def _encode(image: np.ndarray) -> bytes | None:
    ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return jpeg.tobytes() if ok else None


class ImageNode(Node):
    def __init__(self, color_topic: str, depth_topic: str, depth_scale: float) -> None:
        super().__init__("packtug_camera_stream")
        self._depth_scale = depth_scale
        self._color_sub = self.create_subscription(
            Image, color_topic, self._on_color, qos_profile_sensor_data
        )
        self._depth_sub = self.create_subscription(
            Image, depth_topic, self._on_depth, qos_profile_sensor_data
        )

    def _on_color(self, msg: Image) -> None:
        if msg.encoding not in ("rgb8", "bgr8"):
            return
        row = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.step)
        image = row[:, : msg.width * 3].reshape(msg.height, msg.width, 3)
        if msg.encoding == "rgb8":
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        jpeg = _encode(image)
        if jpeg is not None:
            COLOR.update(jpeg, {"encoding": msg.encoding, "width": msg.width, "height": msg.height})

    def _on_depth(self, msg: Image) -> None:
        if msg.encoding != "16UC1":
            return
        row = np.frombuffer(msg.data, dtype=np.uint16).reshape(msg.height, msg.step // 2)
        depth_m = row[:, : msg.width].astype(np.float32) * self._depth_scale
        valid = (depth_m > 0) & np.isfinite(depth_m)
        nearest = float(depth_m[valid].min()) if valid.any() else None
        farthest = float(depth_m[valid].max()) if valid.any() else None

        scaled = np.clip(
            (depth_m - DEPTH_NEAR_M) / (DEPTH_FAR_M - DEPTH_NEAR_M), 0.0, 1.0
        )
        visual = cv2.applyColorMap((scaled * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
        visual[~valid] = (0, 0, 0)
        jpeg = _encode(visual)
        if jpeg is not None:
            DEPTH.update(
                jpeg,
                {
                    "encoding": msg.encoding,
                    "width": msg.width,
                    "height": msg.height,
                    "nearest_m": None if nearest is None else round(nearest, 4),
                    "farthest_m": None if farthest is None else round(farthest, 4),
                    "valid_pixels": int(valid.sum()),
                },
            )


class Handler(BaseHTTPRequestHandler):
    def _headers(self, content_type: str, length: int | None = None) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Access-Control-Allow-Origin", "*")
        if length is not None:
            self.send_header("Content-Length", str(length))

    def _json(self, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(200)
        self._headers("application/json", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _snapshot(self, frame: LatestJpeg) -> None:
        jpeg = frame.get()
        if jpeg is None:
            self.send_error(503, "no frame yet")
            return
        self.send_response(200)
        self._headers("image/jpeg", len(jpeg))
        self.end_headers()
        self.wfile.write(jpeg)

    def _mjpeg(self, frame: LatestJpeg) -> None:
        self.send_response(200)
        self._headers("multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        try:
            while True:
                jpeg = frame.get()
                if jpeg is not None:
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
                time.sleep(1.0 / 20.0)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:
        if self.path.startswith("/mode"):
            if MANAGER is None:
                self._json({"ok": False, "error": "camera process manager is disabled"})
                return
            mode = (parse_qs(urlparse(self.path).query).get("value") or [""])[0].lower()
            if mode not in ("rgb", "depth"):
                self._json({"ok": False, "error": "mode must be rgb or depth"})
                return
            try:
                MANAGER.start(mode)
            except Exception as exc:
                self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
                return
            frame = COLOR if mode == "rgb" else DEPTH
            deadline = time.time() + 10.0
            while time.time() < deadline:
                age = frame.age()
                if age is not None and age < 1.0:
                    break
                time.sleep(0.25)
            self._json(
                {
                    "ok": frame.age() is not None and frame.age() < 1.0,
                    "mode": mode,
                    "age_sec": frame.age(),
                }
            )
            return

        if self.path in ("/", "/health"):
            self._json(
                {
                    "ok": True,
                    "mode": MANAGER.mode if MANAGER is not None else None,
                    "color": COLOR.meta(),
                    "depth": DEPTH.meta(),
                    "stream": "/stream",
                    "depth_stream": "/depth",
                }
            )
            return
        if self.path.startswith("/snapshot"):
            self._snapshot(COLOR)
            return
        if self.path.startswith("/depth_snapshot"):
            self._snapshot(DEPTH)
            return
        if self.path.startswith("/depth_stats"):
            self._json({"ok": True, **DEPTH.meta()})
            return
        if self.path.startswith("/depth"):
            self._mjpeg(DEPTH)
            return
        if self.path.startswith("/stream"):
            self._mjpeg(COLOR)
            return
        self.send_error(404)

    def log_message(self, *args) -> None:
        return


def main() -> None:
    global MANAGER

    parser = argparse.ArgumentParser()
    parser.add_argument("--color-topic", default="/d455/color/image_raw")
    parser.add_argument("--depth-topic", default="/d455/depth/image_raw")
    parser.add_argument("--depth-scale", type=float, default=0.001)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--manage-camera", action="store_true",
                        help="start/restart realsense2_camera for rgb/depth mode switching")
    parser.add_argument("--mode", choices=("rgb", "depth"), default="rgb")
    parser.add_argument("--rgb-profile", default="640x480x15")
    parser.add_argument("--depth-profile", default="480x270x15")
    args = parser.parse_args()

    rclpy.init()
    node = ImageNode(args.color_topic, args.depth_topic, args.depth_scale)
    spin = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin.start()

    if args.manage_camera:
        MANAGER = CameraProcess(args.rgb_profile, args.depth_profile)
        MANAGER.start(args.mode)

    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(
        f"camera streams: mode={MANAGER.mode if MANAGER else 'external'} "
        f"http://0.0.0.0:{args.port}/stream and /depth"
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if MANAGER is not None:
            MANAGER.stop()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
