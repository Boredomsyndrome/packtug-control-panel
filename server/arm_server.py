#!/usr/bin/env python3
"""HTTP bridge between the PackTug control panel and a Niryo Ned1 arm.

Runs in the ``ned`` conda env (pyniryo 1.1.2) and serves:

  * ``/api/arm/*`` — small, rate-limited joint control surface
  * ``../dist``    — the built Vite control panel (SPA fallback)

The Ned1 has no hardware E-stop in this kit, so the bridge deliberately
limits moves: each request may change a joint by at most ``MAX_REQUEST_DEG``
(executed in ``MAX_STEP_DEG`` segments) and the arm speed is capped at
``MAX_VELOCITY`` percent. Motions are performed with PyNiryo's blocking
``move_joints``; keep a hand on the power switch.

A failed move or a collision/overload latches a fault and records the last
confirmed-safe joint position. ``POST /api/arm/recover`` retracts slowly to
that point; ``POST /api/arm/fault/clear`` acknowledges it after inspection.
Manual commands are capped at 60° per joint; the bridge no longer subdivides
them into 15° internal steps.

Usage (from the control-panel folder):

    conda run --no-capture-output -n ned python server/arm_server.py --port 8020
    conda run --no-capture-output -n ned python server/arm_server.py --mock
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

DEFAULT_HOST = os.getenv("ARM_HOST", "10.10.10.10")
CAMERA_STREAM_URL = os.getenv(
    "CAMERA_STREAM_URL",
    "http://100.73.98.69:8080/stream",
)
CAMERA_DEPTH_URL = os.getenv(
    "CAMERA_DEPTH_URL",
    "http://100.73.98.69:8080/depth",
)
CAMERA_DEPTH_STATS_URL = os.getenv(
    "CAMERA_DEPTH_STATS_URL",
    "http://100.73.98.69:8080/depth_stats",
)
CAMERA_MODE_URL = os.getenv(
    "CAMERA_MODE_URL",
    "http://100.73.98.69:8080/mode",
)
CAMERA_SNAPSHOT_URL = os.getenv(
    "CAMERA_SNAPSHOT_URL",
    CAMERA_STREAM_URL.rsplit("/", 1)[0] + "/snapshot",
)
WHEELTEC_WEBCTL_URL = os.getenv("WHEELTEC_WEBCTL_URL", "http://127.0.0.1:8090")
MAX_STEP_DEG = 60.0       # internal segment size; one segment per manual command
MAX_REQUEST_DEG = 60.0    # largest joint change accepted per manual command
MAX_POSE_DEG = 180.0      # largest joint change when recalling a saved pose
MAX_VELOCITY = 5
MAX_VELOCITY_LIMIT = 20
POSES_FILE = Path(__file__).resolve().parent / "arm_poses.json"
SEQUENCES_FILE = Path(__file__).resolve().parent / "arm_sequences.json"
VISION_CALIBRATION_FILE = Path(__file__).resolve().parent / "vision_calibration.json"
HOME_TRANSIT = "__home__"   # built-in safe transit: pyniryo move_to_home_pose()
CONNECTION_ERRORS = {"HostNotReachableException", "ClientNotConnectedException"}
READ_TIMEOUT_S = 5.0        # status/telemetry calls must not block forever
MOTION_TIMEOUT_S = 120.0    # long moves at low speed
TOOL_TIMEOUT_S = 15.0
TOOL_EXTENSION_M = 0.10     # approximate gripper tip beyond the tool frame
DEFAULT_MAX_REACH_M = 0.47
MIN_REACH_M = 0.15
MAX_REACH_M = 0.50
RECOVERY_VELOCITY = 2       # percent; slow retract after a collision/fault
RECOVERY_TOLERANCE_DEG = 0.5
JOINT_VERIFY_TOLERANCE_DEG = 2.0  # blocked/stalled move detection after a segment
COLLISION_MARKERS = (
    "collision",
    "overload",
    "torque",
    "blocked",
    "hardware error",
    "motor error",
)


class _RawCommand:
    """Minimal stand-in for a pyniryo Command enum member.

    ``NiryoRobot.__send_n_receive`` only reads ``command.name``. This lets the
    bridge use newer firmware commands (collision-detected) while keeping the
    pinned pyniryo 1.1.2 client, and fail harmlessly on older robot stacks.
    """

    def __init__(self, name: str) -> None:
        self.name = name


def _rpy_matrix(roll: float, pitch: float, yaw: float) -> np.ndarray:
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    return rz @ ry @ rx


def _transform(xyz: tuple[float, float, float], rpy: tuple[float, float, float]) -> np.ndarray:
    m = np.eye(4)
    m[:3, :3] = _rpy_matrix(*rpy)
    m[:3, 3] = xyz
    return m


def _joint_rot(q: float) -> np.ndarray:
    m = np.eye(4)
    c, s = math.cos(q), math.sin(q)
    m[:2, :2] = [[c, -s], [s, c]]
    return m


def fk_tip(joints_deg: list[float]) -> tuple[float, float, float]:
    """Ned1 URDF forward kinematics for the gripper tip (base frame, metres)."""
    if len(joints_deg) != 6:
        raise ValueError("exactly 6 joint angles are required")
    q = [math.radians(float(v)) for v in joints_deg]
    chain = [
        ((0.0, 0.0, 0.1013), (0.0, 0.0, 0.0)),
        ((0.0, 0.0, 0.065), (math.pi / 2, 0.0, 0.0)),
        ((0.012, 0.221, 0.0), (0.0, 0.0, math.pi / 2)),
        ((0.0325, -0.065, 0.0), (math.pi / 2, 0.0, 0.0)),
        ((0.0, 0.0, 0.17), (-math.pi / 2, 0.0, 0.0)),
        ((0.00925, -0.0197, 0.0), (math.pi / 2, 0.0, 0.0)),
    ]
    m = np.eye(4)
    for i, (xyz, rpy) in enumerate(chain):
        m = m @ _transform(xyz, rpy) @ _joint_rot(q[i])
    tool = m @ _transform((0.0, 0.0, 0.0285), (math.pi, -math.pi / 2, 0.0))
    tip = tool @ _transform((0.0, 0.0, TOOL_EXTENSION_M), (0.0, 0.0, 0.0))
    return float(tip[0, 3]), float(tip[1, 3]), float(tip[2, 3])


def tip_radius(joints_deg: list[float]) -> float:
    x, y, _ = fk_tip(joints_deg)
    return math.hypot(x, y)

# Joint limits, radians. The physical Ned unit accepts a wider J5 range than
# the old installed URDF; the robot-side xacro was opened to 110° alongside
# this table. A 0.5° margin keeps targets off the hard stops.
JOINT_LIMITS_RAD: list[tuple[float, float]] = [
    (-2.949, 2.949),
    (-1.83, 0.61),
    (-1.34390352404, 1.49225651046),
    (-2.089, 2.089),
    (-1.74532925199, math.radians(110.0)),
    (-2.53, 2.53),
]
JOINT_MARGIN_DEG = 0.5
JOINT_LIMITS_DEG = [
    [math.degrees(lo) + JOINT_MARGIN_DEG, math.degrees(hi) - JOINT_MARGIN_DEG]
    for lo, hi in JOINT_LIMITS_RAD
]

log = logging.getLogger("arm-server")


class QrIn(BaseModel):
    x: float
    y: float
    radius: int = Field(default=60, ge=40, le=160)
    roi_x: float | None = None
    roi_y: float | None = None
    roi_w: float | None = None
    roi_h: float | None = None


class VisionPointIn(BaseModel):
    px: float
    py: float
    arm_x: float
    arm_y: float


class VisionPixelIn(BaseModel):
    x: float
    y: float


class ConnectIn(BaseModel):
    ip: str = Field(default=DEFAULT_HOST, min_length=1)


class JogIn(BaseModel):
    joint: int = Field(ge=1, le=6)
    delta_deg: float = Field(ge=-MAX_STEP_DEG, le=MAX_STEP_DEG)


class JointsIn(BaseModel):
    joints_deg: list[float] = Field(min_length=6, max_length=6)


class CalibrateIn(BaseModel):
    confirm: bool = False


class JointIn(BaseModel):
    joint: int = Field(ge=1, le=6)
    angle_deg: float


class VelocityIn(BaseModel):
    percent: int = Field(ge=1, le=MAX_VELOCITY_LIMIT)


class ReachIn(BaseModel):
    max_reach_m: float = Field(ge=MIN_REACH_M, le=MAX_REACH_M)


class PoseIn(BaseModel):
    x: float
    y: float
    z: float
    roll: float
    pitch: float
    yaw: float


class ApplyPoseIn(BaseModel):
    linear: bool = True


class GripperRawIn(BaseModel):
    open: bool
    speed: int = Field(default=500, ge=100, le=1000)


class LearningIn(BaseModel):
    enabled: bool
    confirm: bool = False


class SavePoseIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    joints_deg: list[float] | None = Field(default=None, min_length=6, max_length=6)


class SaveSequenceIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    poses: list[str] = Field(min_length=1, max_length=30)
    dwell_sec: float = Field(default=1.0, ge=0.0, le=10.0)
    transit: str | None = Field(default=None, max_length=40)


class ArmController:
    """Serialises all hardware calls and keeps a UI-friendly cached snapshot."""

    def __init__(self, mock: bool = False) -> None:
        self.mock = mock
        self._state = threading.Lock()
        self._motion = threading.Lock()
        self._robot: Any = None
        self._poses_lock = threading.Lock()
        self._sequences_lock = threading.Lock()
        self.connected = False
        self.host: str | None = None
        self.joints_deg: list[float] | None = None
        self.pose: dict[str, float] | None = None
        self.calibration_needed: bool | None = None
        self.learning_mode: bool | None = None
        self.busy = False
        self.last_error: str | None = None
        self.max_velocity = MAX_VELOCITY
        self.max_reach_m = DEFAULT_MAX_REACH_M
        self.updated_at: float | None = None
        self.tool: str | None = None
        self.gripper: str = "unknown"
        self.fault: str | None = None
        self.collision: bool = False
        self.recovery_available: bool = False
        self.recovery_target_deg: list[float] | None = None
        self.last_good_joints: list[float] | None = None
        self.collision_api_supported: bool | None = None

    # ---------- snapshot ----------

    def snapshot(self) -> dict[str, Any]:
        with self._state:
            return {
                "connected": self.connected,
                "mock": self.mock,
                "host": self.host,
                "joints_deg": list(self.joints_deg) if self.joints_deg is not None else None,
                "pose": dict(self.pose) if self.pose is not None else None,
                "calibration_needed": self.calibration_needed,
                "learning_mode": self.learning_mode,
                "max_velocity": self.max_velocity,
                "velocity_limit": MAX_VELOCITY_LIMIT,
                "max_reach_m": round(self.max_reach_m, 3),
                "tool_extension_m": TOOL_EXTENSION_M,
                "reach_m": round(tip_radius(self.joints_deg), 3) if self.joints_deg else None,
                "max_step_deg": MAX_STEP_DEG,
                "max_request_deg": MAX_REQUEST_DEG,
                "max_pose_deg": MAX_POSE_DEG,
                "limits_deg": JOINT_LIMITS_DEG,
                "tool": self.tool,
                "gripper": self.gripper,
                "busy": self.busy,
                "error": self.last_error,
                "fault": self.fault,
                "collision": self.collision,
                "recovery_available": self.recovery_available,
                "recovery_target_deg": (
                    list(self.recovery_target_deg)
                    if self.recovery_target_deg is not None
                    else None
                ),
                "collision_api": (
                    "hardware" if self.collision_api_supported
                    else "motion-error" if self.collision_api_supported is False
                    else "unknown"
                ),
                "updated_at": self.updated_at,
            }

    def hardware_status(self) -> dict[str, Any]:
        with self._motion:
            with self._state:
                if not self.connected:
                    raise HTTPException(409, "arm is not connected")
                if not self.mock and self._robot is None:
                    raise HTTPException(409, "arm is not connected")
            if self.mock:
                return {"mock": True, "hardware_version": "mock", "error_message": None,
                        "hardware_errors": [], "motors_temperature": [], "motors_voltage": []}
            try:
                with self._socket_timeout(READ_TIMEOUT_S):
                    hw = self._robot.get_hardware_status()
            except Exception as exc:
                with self._state:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    if type(exc).__name__ in CONNECTION_ERRORS:
                        self.connected = False
                raise HTTPException(502, self.last_error) from exc
            return {
                "mock": False,
                "hardware_version": getattr(hw, "hardware_version", None),
                "rpi_temperature": getattr(hw, "rpi_temperature", None),
                "connection_up": getattr(hw, "connection_up", None),
                "error_message": getattr(hw, "error_message", None),
                "calibration_needed": getattr(hw, "calibration_needed", None),
                "calibration_in_progress": getattr(hw, "calibration_in_progress", None),
                "motor_names": getattr(hw, "motor_names", []),
                "motor_types": getattr(hw, "motor_types", []),
                "motors_temperature": getattr(hw, "motors_temperature", []),
                "motors_voltage": getattr(hw, "motors_voltage", []),
                "hardware_errors": getattr(hw, "hardware_errors", []),
            }

    def status(self) -> dict[str, Any]:
        if self.connected and not self.mock and not self.busy:
            if self._motion.acquire(blocking=False):
                try:
                    self._read_locked()
                    with self._state:
                        self.last_error = None
                except Exception as exc:  # keep serving the cached snapshot
                    with self._state:
                        self.last_error = f"{type(exc).__name__}: {exc}"
                        if type(exc).__name__ in CONNECTION_ERRORS:
                            self.connected = False
                finally:
                    self._motion.release()
        return self.snapshot()

    # ---------- connection ----------

    def connect(self, host: str) -> dict[str, Any]:
        with self._motion:
            self._close_locked()
            if self.mock:
                with self._state:
                    self.connected = True
                    self.host = host
                    self.joints_deg = [0.0, -15.0, 20.0, 0.0, 0.0, 0.0]
                    self.pose = {"x": 0.3, "y": 0.0, "z": 0.2, "roll": 180.0,
                                 "pitch": 0.0, "yaw": 90.0}
                    self.calibration_needed = False
                    self.learning_mode = False
                    self.tool = "GRIPPER_1"
                    self.gripper = "unknown"
                    self.fault = None
                    self.collision = False
                    self.recovery_available = False
                    self.recovery_target_deg = None
                    self.last_good_joints = list(self.joints_deg)
                    self.last_error = None
                    self.updated_at = time.time()
                log.info("mock arm connected (%s)", host)
                return self.snapshot()

            from pyniryo import NiryoRobot

            log.info("connecting to Ned1 at %s ...", host)
            robot = NiryoRobot(host)
            sock = getattr(robot, "_NiryoRobot__client_socket", None)
            if sock is not None:
                try:
                    sock.settimeout(TOOL_TIMEOUT_S)
                except OSError:
                    pass
            try:
                robot.set_arm_max_velocity(self.max_velocity)
                tool_name = None
                try:
                    robot.update_tool()
                    tool_name = getattr(robot.get_current_tool_id(), "name", None)
                except Exception as exc:
                    log.warning("tool detection failed: %s", exc)
                with self._state:
                    self._robot = robot
                self._read_locked()
                self._probe_collision_api_locked()
                with self._state:
                    self.connected = True
                    self.host = host
                    self.tool = tool_name
                    self.gripper = "unknown"
                    self.fault = None
                    self.collision = False
                    self.recovery_available = False
                    self.recovery_target_deg = None
                    self.last_good_joints = (
                        list(self.joints_deg) if self.joints_deg is not None else None
                    )
                    self.last_error = None
            except Exception:
                with self._state:
                    self._robot = None
                try:
                    robot.close_connection()
                except Exception:
                    pass
                raise
            log.info("Ned1 connected at %s", host)
            return self.snapshot()

    def disconnect(self) -> dict[str, Any]:
        with self._motion:
            self._close_locked()
        return self.snapshot()

    def _close_locked(self) -> None:
        robot, self._robot = self._robot, None
        if robot is not None:
            try:
                robot.close_connection()
            except Exception:
                pass
        with self._state:
            self.connected = False
            self.host = None
            self.joints_deg = None
            self.pose = None
            self.calibration_needed = None
            self.learning_mode = None
            self.tool = None
            self.gripper = "unknown"
            self.busy = False
            self.fault = None
            self.collision = False
            self.recovery_available = False
            self.recovery_target_deg = None
            self.last_good_joints = None
            self.collision_api_supported = None

    # ---------- reads ----------

    @contextlib.contextmanager
    def _socket_timeout(self, seconds: float):
        """Bound PyNiryo socket calls; its default recv timeout is infinite."""
        sock = getattr(self._robot, "_NiryoRobot__client_socket", None)
        old: float | None = None
        if sock is not None:
            try:
                old = sock.gettimeout()
                sock.settimeout(seconds)
            except OSError:
                sock = None
        try:
            yield
        finally:
            if sock is not None:
                try:
                    sock.settimeout(old)
                except OSError:
                    pass

    def _read_locked(self) -> None:
        if self.mock:
            return
        robot = self._robot
        if robot is None:
            raise ConnectionError("not connected")
        with self._socket_timeout(READ_TIMEOUT_S):
            joints = [math.degrees(float(v)) for v in robot.get_joints()]
            p = robot.get_pose()
            pose = {
                "x": float(p.x), "y": float(p.y), "z": float(p.z),
                "roll": math.degrees(float(p.roll)),
                "pitch": math.degrees(float(p.pitch)),
                "yaw": math.degrees(float(p.yaw)),
            }
            calibration_needed = bool(robot.need_calibration())
            learning_mode = bool(robot.get_learning_mode())
        with self._state:
            self.joints_deg = joints
            self.pose = pose
            self.calibration_needed = calibration_needed
            self.learning_mode = learning_mode
            self.updated_at = time.time()

    def _current_joints_locked(self) -> list[float]:
        if self.mock:
            with self._state:
                return list(self.joints_deg or [0.0] * 6)
        self._read_locked()
        with self._state:
            return list(self.joints_deg or [])

    # ---------- fault / collision detection ----------

    def _probe_collision_api_locked(self) -> None:
        """Try the newer GET_COLLISION_DETECTED command once on connect.

        pyniryo 1.1.2 predates the public collision property, but the robot
        firmware may still support the command. If it does, use it as the
        primary detector; otherwise fall back to failed-motion classification.
        """
        if self.mock:
            return
        detected = self._query_collision_flag_locked()
        if detected:
            self._clear_collision_flag_locked()

    def _query_collision_flag_locked(self) -> bool | None:
        if self.mock or self._robot is None:
            return None
        if self.collision_api_supported is False:
            return None
        send = getattr(self._robot, "_NiryoRobot__send_n_receive", None)
        if send is None:
            with self._state:
                self.collision_api_supported = False
            return None
        try:
            with self._socket_timeout(READ_TIMEOUT_S):
                value = send(_RawCommand("GET_COLLISION_DETECTED"))
        except Exception as exc:
            text = str(exc).lower()
            if "unknown" in text or "not implemented" in text or "unrecognized" in text:
                with self._state:
                    self.collision_api_supported = False
            return None
        with self._state:
            self.collision_api_supported = True
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
        return bool(value)

    def _clear_collision_flag_locked(self) -> None:
        if self.mock or self.collision_api_supported is not True:
            return
        send = getattr(self._robot, "_NiryoRobot__send_n_receive", None)
        if send is None:
            return
        try:
            with self._socket_timeout(READ_TIMEOUT_S):
                send(_RawCommand("CLEAR_COLLISION_DETECTED"))
        except Exception as exc:
            log.debug("could not clear robot collision flag: %s", exc)

    def _hardware_fault_message_locked(self) -> str | None:
        """Return a non-empty hardware error message, if the robot reports one."""
        if self.mock or self._robot is None:
            return None
        try:
            with self._socket_timeout(READ_TIMEOUT_S):
                hw = self._robot.get_hardware_status()
        except Exception as exc:
            log.debug("hardware status read failed: %s", exc)
            return None
        raw_errors = getattr(hw, "hardware_errors", None) or []
        if isinstance(raw_errors, str):
            raw_errors = [raw_errors]
        errors = [
            str(item).strip()
            for item in raw_errors
            if str(item).strip().lower() not in ("", "0", "none", "null")
        ]
        message = str(getattr(hw, "error_message", "") or "").strip()
        if message.lower() in ("", "none", "null", "ok"):
            message = ""
        if errors or message:
            return message or "; ".join(errors)
        return None

    @staticmethod
    def _is_collision_text(text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in COLLISION_MARKERS)

    def _record_motion_fault_locked(self, exc: BaseException, actual: list[float] | None = None) -> None:
        """Latch a motion fault and remember the last known-safe joint position."""
        text = str(exc).strip() or type(exc).__name__
        detected = self._query_collision_flag_locked()
        collision = bool(detected) or self._is_collision_text(text)
        hardware = self._hardware_fault_message_locked()
        if hardware and hardware.lower() not in text.lower():
            text = f"{text} (hardware: {hardware})"
        with self._state:
            if actual is not None:
                self.joints_deg = [float(v) for v in actual]
            target = list(self.last_good_joints) if self.last_good_joints is not None else None
            current = self.joints_deg
            self.fault = text
            self.collision = collision
            self.recovery_target_deg = target
            self.recovery_available = bool(
                target is not None
                and current is not None
                and max(abs(a - b) for a, b in zip(current, target)) > RECOVERY_TOLERANCE_DEG
            )
            self.last_error = text
            self.updated_at = time.time()
        if detected:
            self._clear_collision_flag_locked()
        log.error("arm motion fault%s: %s", " (collision)" if collision else "", text)

    def _read_after_fault_locked(self) -> list[float] | None:
        """Best-effort read of the real joints after a failed motion."""
        if self.mock:
            with self._state:
                return list(self.joints_deg) if self.joints_deg is not None else None
        try:
            self._read_locked()
        except Exception as exc:
            log.warning("could not read joints after fault: %s", exc)
        with self._state:
            return list(self.joints_deg) if self.joints_deg is not None else None

    # ---------- motion ----------

    def _check_motion_locked(self) -> None:
        with self._state:
            if not self.connected:
                raise HTTPException(409, "arm is not connected")
            if not self.mock and self._robot is None:
                raise HTTPException(409, "arm is not connected")
            if self.calibration_needed:
                raise HTTPException(409, "calibration required — run ned_calibrate.py --go")
            if self.learning_mode:
                raise HTTPException(409, "motors are released — enable motors first")
            if self.fault:
                raise HTTPException(
                    409,
                    "arm is in a motion fault state — press Recover to retract to the "
                    "last safe point before moving again",
                )

    def _check_tool_locked(self) -> None:
        with self._state:
            if not self.connected:
                raise HTTPException(409, "arm is not connected")
            if self.learning_mode:
                raise HTTPException(409, "motors are released — enable motors first")
            if self.fault:
                raise HTTPException(409, "arm is in a motion fault state — recover first")
            tool = self.tool
        if not self.mock and (not tool or "GRIPPER" not in tool):
            raise HTTPException(409, "no gripper detected — attach it and rescan the tool")

    def _validate_target(
        self, target: list[float], current: list[float], max_delta: float = MAX_REQUEST_DEG
    ) -> None:
        if len(target) != 6:
            raise HTTPException(400, "exactly 6 joint angles are required")
        for i, value in enumerate(target):
            if not math.isfinite(value):
                raise HTTPException(400, f"joint {i + 1}: angle must be finite")
            lo, hi = JOINT_LIMITS_DEG[i]
            if value < lo - 1e-6 or value > hi + 1e-6:
                raise HTTPException(
                    400, f"joint {i + 1}: {value:.1f}° outside limits [{lo:.1f}, {hi:.1f}]"
                )
            if abs(value - current[i]) > max_delta + 1e-6:
                raise HTTPException(
                    400,
                    f"joint {i + 1}: move {abs(value - current[i]):.1f}° exceeds "
                    f"the {max_delta:.0f}° limit for this command",
                )

    def _path_waypoints(self, current: list[float], target: list[float]) -> list[list[float]]:
        max_delta = max(abs(t - c) for c, t in zip(current, target))
        segments = max(1, math.ceil(max_delta / MAX_STEP_DEG))
        return [
            [c + (t - c) * (i / segments) for c, t in zip(current, target)]
            for i in range(1, segments + 1)
        ]

    def _check_path_reach(self, current: list[float], target: list[float]) -> None:
        waypoints = self._path_waypoints(current, target)
        for i, waypoint in enumerate(waypoints, start=1):
            radius = tip_radius(waypoint)
            if radius > self.max_reach_m + 1e-6:
                raise HTTPException(
                    400,
                    f"path exceeds max horizontal reach {self.max_reach_m:.3f} m "
                    f"at segment {i}/{len(waypoints)} (tip radius {radius:.3f} m); "
                    "use linear path or move the pose inward",
                )

    def _execute_move_locked(self, waypoints: list[list[float]]) -> None:
        """Run the move in <=MAX_STEP_DEG segments so a slider drag is smooth."""
        for i, waypoint in enumerate(waypoints, start=1):
            try:
                with self._socket_timeout(MOTION_TIMEOUT_S):
                    self._robot.move_joints([math.radians(v) for v in waypoint])
                if self._query_collision_flag_locked():
                    raise RuntimeError(
                        f"robot reported a collision after segment {i}/{len(waypoints)}"
                    )
            except Exception as exc:
                actual = self._read_after_fault_locked()
                self._record_motion_fault_locked(exc, actual)
                raise RuntimeError(
                    f"segment {i}/{len(waypoints)} failed at joints "
                    f"{[round(v, 1) for v in waypoint]}: {exc}"
                ) from exc
            with self._state:
                self.joints_deg = list(waypoint)
                self.last_good_joints = list(waypoint)
                self.updated_at = time.time()

    def _move_locked(
        self,
        target_deg: list[float],
        current: list[float] | None = None,
        max_delta: float = MAX_REQUEST_DEG,
    ) -> dict[str, Any]:
        with self._state:
            self.busy = True
        try:
            if current is None:
                current = self._current_joints_locked()
            with self._state:
                self.last_good_joints = list(current)
            self._validate_target(target_deg, current, max_delta)
            waypoints = self._path_waypoints(current, target_deg)
            self._check_path_reach(current, target_deg)
            if self.mock:
                time.sleep(0.15)
                with self._state:
                    self.joints_deg = [float(v) for v in target_deg]
                    self.last_good_joints = [float(v) for v in target_deg]
                    self.updated_at = time.time()
            else:
                self._execute_move_locked(waypoints)
                self._read_locked()
                with self._state:
                    self.last_good_joints = (
                        list(self.joints_deg) if self.joints_deg is not None else list(target_deg)
                    )
            with self._state:
                self.last_error = None
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("motion failed")
            with self._state:
                self.last_error = self.fault or f"{type(exc).__name__}: {exc}"
                if type(exc).__name__ in CONNECTION_ERRORS:
                    self.connected = False
            raise HTTPException(502, self.last_error) from exc
        finally:
            with self._state:
                self.busy = False
        return self.snapshot()

    def move_joints(self, target_deg: list[float]) -> dict[str, Any]:
        with self._motion:
            self._check_motion_locked()
            return self._move_locked(target_deg)

    def move_joint(self, joint: int, angle_deg: float) -> dict[str, Any]:
        with self._motion:
            self._check_motion_locked()
            current = self._current_joints_locked()
            target = list(current)
            target[joint - 1] = angle_deg
            return self._move_locked(target, current)

    def move_pose(self, pose: PoseIn) -> dict[str, Any]:
        values = (pose.x, pose.y, pose.z, pose.roll, pose.pitch, pose.yaw)
        if not all(math.isfinite(v) for v in values):
            raise HTTPException(400, "pose values must be finite")
        if math.hypot(pose.x, pose.y) > self.max_reach_m + 1e-6:
            raise HTTPException(
                400,
                f"horizontal reach {math.hypot(pose.x, pose.y):.3f} m exceeds max "
                f"{self.max_reach_m:.3f} m",
            )
        with self._motion:
            self._check_motion_locked()
            with self._state:
                self.busy = True
            try:
                if self.mock:
                    time.sleep(0.2)
                    with self._state:
                        self.pose = {
                            "x": pose.x, "y": pose.y, "z": pose.z,
                            "roll": pose.roll, "pitch": pose.pitch, "yaw": pose.yaw,
                        }
                        self.updated_at = time.time()
                else:
                    target = SimpleNamespace(**pose.model_dump())
                    with self._socket_timeout(MOTION_TIMEOUT_S):
                        self._send_linear_pose(target)
                    self._read_locked()
                with self._state:
                    self.last_error = None
            except HTTPException:
                raise
            except Exception as exc:
                log.exception("pose motion failed")
                with self._state:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    if type(exc).__name__ in CONNECTION_ERRORS:
                        self.connected = False
                raise HTTPException(502, self.last_error) from exc
            finally:
                with self._state:
                    self.busy = False
        return self.snapshot()

    def jog(self, joint: int, delta_deg: float) -> dict[str, Any]:
        if abs(delta_deg) < 1e-6:
            return self.snapshot()
        with self._motion:
            self._check_motion_locked()
            current = self._current_joints_locked()
            target = list(current)
            target[joint - 1] += delta_deg
            return self._move_locked(target, current)

    def move_home(self) -> dict[str, Any]:
        """Fold the arm to the PyNiryo home pose (safe transit / park)."""
        with self._motion:
            self._check_motion_locked()
            with self._state:
                self.busy = True
            try:
                current = self._current_joints_locked()
                with self._state:
                    self.last_good_joints = list(current)
                if self.mock:
                    time.sleep(0.2)
                    with self._state:
                        self.joints_deg = [0.0, -15.0, 20.0, 0.0, 0.0, 0.0]
                        self.last_good_joints = list(self.joints_deg)
                        self.updated_at = time.time()
                else:
                    with self._socket_timeout(MOTION_TIMEOUT_S):
                        self._robot.move_to_home_pose()
                    self._read_locked()
                    with self._state:
                        self.last_good_joints = (
                            list(self.joints_deg) if self.joints_deg is not None else None
                        )
                with self._state:
                    self.last_error = None
            except HTTPException:
                raise
            except Exception as exc:
                log.exception("home move failed")
                actual = self._read_after_fault_locked()
                self._record_motion_fault_locked(exc, actual)
                with self._state:
                    self.last_error = self.fault or f"{type(exc).__name__}: {exc}"
                    if type(exc).__name__ in CONNECTION_ERRORS:
                        self.connected = False
                raise HTTPException(502, self.last_error) from exc
            finally:
                with self._state:
                    self.busy = False
        return self.snapshot()

    def recover(self) -> dict[str, Any]:
        """Retract along the last commanded path to the last known-safe joints.

        This is deliberately a short, slow reverse move, not an automatic
        ``home`` sweep: after a collision the arm is already touching an object,
        so the safest first action is to undo the failed segment. The fault is
        cleared only after the retract succeeds; otherwise the operator must
        inspect the arm and release the motors manually.
        """
        with self._motion:
            with self._state:
                if not self.connected:
                    raise HTTPException(409, "arm is not connected")
                if not self.mock and self._robot is None:
                    raise HTTPException(409, "arm is not connected")
                if self.calibration_needed:
                    raise HTTPException(409, "calibration required")
                if self.learning_mode:
                    raise HTTPException(409, "motors are released — enable motors first")
                target = list(self.last_good_joints) if self.last_good_joints is not None else None
            if target is None:
                raise HTTPException(
                    409,
                    "no safe retract point recorded — release the motors and move the arm "
                    "clear by hand, then clear the fault",
                )
            with self._state:
                self.busy = True
            old_velocity = self.max_velocity
            try:
                current = self._current_joints_locked()
                if max(abs(a - b) for a, b in zip(current, target)) <= RECOVERY_TOLERANCE_DEG:
                    with self._state:
                        self.joints_deg = list(target)
                        self.last_good_joints = list(target)
                        self.fault = None
                        self.collision = False
                        self.recovery_available = False
                        self.recovery_target_deg = None
                        self.last_error = None
                        self.busy = False
                        self.updated_at = time.time()
                    return self.snapshot()
                if self.mock:
                    time.sleep(0.15)
                    with self._state:
                        self.joints_deg = list(target)
                else:
                    with self._socket_timeout(TOOL_TIMEOUT_S):
                        self._robot.set_arm_max_velocity(min(old_velocity, RECOVERY_VELOCITY))
                    waypoints = self._path_waypoints(current, target)
                    self._execute_move_locked(waypoints)
                    self._read_locked()
                with self._state:
                    self.last_good_joints = (
                        list(self.joints_deg) if self.joints_deg is not None else list(target)
                    )
                    self.fault = None
                    self.collision = False
                    self.recovery_available = False
                    self.recovery_target_deg = None
                    self.last_error = None
                    self.updated_at = time.time()
                log.info("arm recovered to last safe point at %s", self.last_good_joints)
            except HTTPException:
                raise
            except Exception as exc:
                log.exception("arm recovery failed")
                actual = self._read_after_fault_locked()
                self._record_motion_fault_locked(exc, actual)
                with self._state:
                    self.last_error = self.fault or f"{type(exc).__name__}: {exc}"
                raise HTTPException(502, f"recovery failed: {self.last_error}") from exc
            finally:
                if not self.mock:
                    try:
                        with self._socket_timeout(TOOL_TIMEOUT_S):
                            self._robot.set_arm_max_velocity(old_velocity)
                    except Exception as exc:
                        log.warning("could not restore arm speed after recovery: %s", exc)
                with self._state:
                    self.busy = False
        return self.snapshot()

    def clear_fault(self) -> dict[str, Any]:
        """Acknowledge a fault after the operator has inspected the arm."""
        with self._motion:
            with self._state:
                if not self.connected:
                    raise HTTPException(409, "arm is not connected")
            self._clear_collision_flag_locked()
            if not self.mock:
                try:
                    self._read_locked()
                except Exception as exc:
                    log.warning("could not read joints while clearing fault: %s", exc)
            with self._state:
                self.fault = None
                self.collision = False
                self.recovery_available = False
                self.recovery_target_deg = None
                self.last_good_joints = (
                    list(self.joints_deg) if self.joints_deg is not None else None
                )
                self.last_error = None
                self.updated_at = time.time()
        return self.snapshot()

    def calibrate(self) -> dict[str, Any]:
        """Auto-calibrate: the arm slowly sweeps its full range of motion.

        No hardware E-stop in this kit, so the caller must have confirmed the
        workspace is clear. This blocks for roughly 30 seconds.
        """
        with self._motion:
            with self._state:
                if not self.connected:
                    raise HTTPException(409, "arm is not connected")
            with self._state:
                self.busy = True
            try:
                if self.mock:
                    time.sleep(1.2)
                    with self._state:
                        self.joints_deg = [0.0, -15.0, 20.0, 0.0, 0.0, 0.0]
                        self.pose = {"x": 0.3, "y": 0.0, "z": 0.2, "roll": 180.0,
                                     "pitch": 0.0, "yaw": 90.0}
                        self.calibration_needed = False
                        self.learning_mode = False
                        self.updated_at = time.time()
                else:
                    with self._socket_timeout(TOOL_TIMEOUT_S):
                        needs_release = self._robot.get_learning_mode()
                    if needs_release:
                        with self._socket_timeout(TOOL_TIMEOUT_S):
                            self._robot.set_learning_mode(False)
                    with self._socket_timeout(MOTION_TIMEOUT_S):
                        self._robot.calibrate_auto()
                    self._read_locked()
                with self._state:
                    self.last_error = None
            except HTTPException:
                raise
            except Exception as exc:
                with self._state:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                raise HTTPException(502, self.last_error) from exc
            finally:
                with self._state:
                    self.busy = False
        return self.snapshot()

    def enable_motors(self) -> dict[str, Any]:
        with self._motion:
            with self._state:
                if not self.connected:
                    raise HTTPException(409, "arm is not connected")
            if self.mock:
                with self._state:
                    self.learning_mode = False
                    self.updated_at = time.time()
                return self.snapshot()
            try:
                with self._socket_timeout(TOOL_TIMEOUT_S):
                    self._robot.set_learning_mode(False)
                self._read_locked()
                with self._state:
                    self.last_error = None
            except Exception as exc:
                with self._state:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                raise HTTPException(502, self.last_error) from exc
            return self.snapshot()

    def set_velocity(self, percent: int) -> dict[str, Any]:
        if percent < 1 or percent > MAX_VELOCITY_LIMIT:
            raise HTTPException(400, f"speed must be between 1 and {MAX_VELOCITY_LIMIT}%")
        with self._motion:
            with self._state:
                connected = self.connected and self._robot is not None
            if connected and not self.mock:
                try:
                    with self._socket_timeout(TOOL_TIMEOUT_S):
                        self._robot.set_arm_max_velocity(percent)
                except Exception as exc:
                    log.exception("set velocity failed")
                    with self._state:
                        self.last_error = f"{type(exc).__name__}: {exc}"
                    raise HTTPException(502, self.last_error) from exc
            with self._state:
                self.max_velocity = percent
                self.last_error = None
                self.updated_at = time.time()
        return self.snapshot()

    def set_reach(self, max_reach_m: float) -> dict[str, Any]:
        with self._state:
            self.max_reach_m = round(float(max_reach_m), 3)
            self.updated_at = time.time()
        return self.snapshot()

    def set_learning(self, enabled: bool) -> dict[str, Any]:
        """Release motors (manual posing) or re-engage them."""
        with self._motion:
            with self._state:
                if not self.connected:
                    raise HTTPException(409, "arm is not connected")
            with self._state:
                self.busy = True
            try:
                if self.mock:
                    time.sleep(0.15)
                    with self._state:
                        self.learning_mode = enabled
                        self.updated_at = time.time()
                elif enabled:
                    with self._socket_timeout(TOOL_TIMEOUT_S):
                        self._robot.set_learning_mode(True)
                    with self._state:
                        self.learning_mode = True
                        self.updated_at = time.time()
                else:
                    with self._socket_timeout(TOOL_TIMEOUT_S):
                        self._robot.set_learning_mode(False)
                    self._read_locked()
                with self._state:
                    self.last_error = None
            except HTTPException:
                raise
            except Exception as exc:
                with self._state:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                raise HTTPException(502, self.last_error) from exc
            finally:
                with self._state:
                    self.busy = False
        return self.snapshot()

    def _send_linear_pose(self, pose_obj: Any) -> None:
        """Older Ned1 firmware expects MOVE_LINEAR_POSE with 6 params (no frame).

        pyniryo's public move_linear_pose() appends an empty frame name, so the
        robot rejects it with "6 parameters expected but 7 given".
        """
        from pyniryo.api.enums_communication import Command

        send = getattr(self._robot, "_NiryoRobot__send_n_receive", None)
        if send is None:
            raise RuntimeError("pyniryo TCP client layout changed; cannot send raw pose")
        send(
            Command.MOVE_LINEAR_POSE,
            float(pose_obj.x),
            float(pose_obj.y),
            float(pose_obj.z),
            float(pose_obj.roll),
            float(pose_obj.pitch),
            float(pose_obj.yaw),
        )

    def _gripper_action(self, close: bool) -> dict[str, Any]:
        with self._motion:
            self._check_tool_locked()
            with self._state:
                self.busy = True
            try:
                if self.mock:
                    time.sleep(0.2)
                elif close:
                    # grasp/release use the robot's parameterless tool commands.
                    # open_gripper()/close_gripper() send 3 params that older
                    # Ned1 firmware rejects with "1 parameters expected".
                    with self._socket_timeout(TOOL_TIMEOUT_S):
                        self._robot.grasp_with_tool()
                else:
                    with self._socket_timeout(TOOL_TIMEOUT_S):
                        self._robot.release_with_tool()
                with self._state:
                    self.gripper = "closed" if close else "open"
                    self.last_error = None
                    self.updated_at = time.time()
            except HTTPException:
                raise
            except Exception as exc:
                log.exception("gripper command failed")
                with self._state:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    if type(exc).__name__ in CONNECTION_ERRORS:
                        self.connected = False
                raise HTTPException(502, self.last_error) from exc
            finally:
                with self._state:
                    self.busy = False
        return self.snapshot()

    def open_gripper(self) -> dict[str, Any]:
        return self._gripper_action(False)

    def close_gripper(self) -> dict[str, Any]:
        return self._gripper_action(True)

    def raw_gripper(self, open_gripper: bool, speed: int) -> dict[str, Any]:
        """Send the firmware's 1-parameter OPEN/CLOSE_GRIPPER command."""
        with self._motion:
            self._check_tool_locked()
            with self._state:
                self.busy = True
            try:
                if self.mock:
                    time.sleep(0.2)
                else:
                    from pyniryo.api.enums_communication import Command

                    send = getattr(self._robot, "_NiryoRobot__send_n_receive", None)
                    if send is None:
                        raise RuntimeError("pyniryo TCP client layout changed")
                    command = Command.OPEN_GRIPPER if open_gripper else Command.CLOSE_GRIPPER
                    with self._socket_timeout(TOOL_TIMEOUT_S):
                        send(command, int(speed))
                with self._state:
                    self.gripper = "open" if open_gripper else "closed"
                    self.last_error = None
                    self.updated_at = time.time()
            except HTTPException:
                raise
            except Exception as exc:
                log.exception("raw gripper command failed")
                with self._state:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    if type(exc).__name__ in CONNECTION_ERRORS:
                        self.connected = False
                raise HTTPException(502, self.last_error) from exc
            finally:
                with self._state:
                    self.busy = False
        return self.snapshot()

    def scan_tool(self) -> dict[str, Any]:
        with self._motion:
            with self._state:
                if not self.connected:
                    raise HTTPException(409, "arm is not connected")
            if self.mock:
                with self._state:
                    self.tool = "GRIPPER_1"
                    self.gripper = "unknown"
                    self.updated_at = time.time()
                return self.snapshot()
            try:
                with self._socket_timeout(TOOL_TIMEOUT_S):
                    self._robot.update_tool()
                    tool_name = getattr(self._robot.get_current_tool_id(), "name", None)
            except Exception as exc:
                with self._state:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                raise HTTPException(502, self.last_error) from exc
            with self._state:
                self.tool = tool_name
                self.gripper = "unknown"
                self.last_error = None
                self.updated_at = time.time()
        return self.snapshot()

    # ---------- saved poses ----------

    def _load_poses_locked(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(POSES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        poses = data.get("poses", []) if isinstance(data, dict) else data
        if not isinstance(poses, list):
            return []
        cleaned: list[dict[str, Any]] = []
        for entry in poses:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            joints = entry.get("joints_deg")
            if (
                isinstance(name, str)
                and name
                and isinstance(joints, list)
                and len(joints) == 6
                and all(isinstance(v, (int, float)) and math.isfinite(float(v)) for v in joints)
            ):
                cleaned.append(
                    {
                        "name": name,
                        "joints_deg": [
                            round(max(JOINT_LIMITS_DEG[i][0], min(JOINT_LIMITS_DEG[i][1], float(v))), 3)
                            for i, v in enumerate(joints)
                        ],
                        "radius_m": round(
                            tip_radius(
                                [
                                    max(JOINT_LIMITS_DEG[i][0], min(JOINT_LIMITS_DEG[i][1], float(v)))
                                    for i, v in enumerate(joints)
                                ]
                            ),
                            3,
                        ),
                        "created_at": entry.get("created_at"),
                        "updated_at": entry.get("updated_at"),
                    }
                )
        return cleaned

    def _write_poses_locked(self, poses: list[dict[str, Any]]) -> None:
        POSES_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = POSES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"poses": poses}, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(POSES_FILE)

    def _normalise_pose(self, joints: list[float]) -> list[float]:
        if len(joints) != 6:
            raise HTTPException(400, "exactly 6 joint angles are required")
        out = []
        for i, value in enumerate(joints):
            v = float(value)
            if not math.isfinite(v):
                raise HTTPException(400, f"joint {i + 1}: angle must be finite")
            lo, hi = JOINT_LIMITS_DEG[i]
            out.append(round(max(lo, min(hi, v)), 3))
        return out

    def list_poses(self) -> dict[str, Any]:
        with self._poses_lock:
            return {"poses": self._load_poses_locked()}

    def save_pose(self, name: str, joints_deg: list[float] | None = None) -> dict[str, Any]:
        name = name.strip()
        if not name or len(name) > 40 or "/" in name:
            raise HTTPException(400, "pose name must be 1-40 characters without '/'")
        if joints_deg is None:
            with self._motion:
                with self._state:
                    if not self.connected:
                        raise HTTPException(409, "arm is not connected")
                joints = self._current_joints_locked()
        else:
            joints = list(joints_deg)
        joints = self._normalise_pose(joints)
        now = datetime.now(timezone.utc).isoformat()
        with self._poses_lock:
            poses = self._load_poses_locked()
            existing = next((p for p in poses if p["name"] == name), None)
            if existing is not None:
                existing["joints_deg"] = joints
                existing["updated_at"] = now
                pose = existing
            else:
                pose = {"name": name, "joints_deg": joints, "created_at": now, "updated_at": now}
                poses.append(pose)
            self._write_poses_locked(poses)
        return {"pose": pose, "poses": poses}

    def delete_pose(self, name: str) -> dict[str, Any]:
        with self._poses_lock:
            poses = self._load_poses_locked()
            remaining = [p for p in poses if p["name"] != name]
            if len(remaining) == len(poses):
                raise HTTPException(404, f"pose not found: {name}")
            self._write_poses_locked(remaining)
        return {"poses": remaining}

    def apply_pose(self, name: str, linear: bool = True) -> dict[str, Any]:
        with self._poses_lock:
            poses = self._load_poses_locked()
            pose = next((p for p in poses if p["name"] == name), None)
        if pose is None:
            raise HTTPException(404, f"pose not found: {name}")
        target = pose["joints_deg"]
        with self._motion:
            self._check_motion_locked()
            current = self._current_joints_locked()
            if linear:
                with self._state:
                    self.busy = True
                    self.last_good_joints = list(current)
                try:
                    radius = tip_radius(target)
                    if radius > self.max_reach_m + 1e-6:
                        raise HTTPException(
                            400,
                            f"horizontal reach {radius:.3f} m exceeds max {self.max_reach_m:.3f} m",
                        )
                    if self.mock:
                        time.sleep(0.2)
                        with self._state:
                            self.joints_deg = [float(v) for v in target]
                            self.last_good_joints = [float(v) for v in target]
                            self.updated_at = time.time()
                    else:
                        with self._socket_timeout(TOOL_TIMEOUT_S):
                            target_pose = self._robot.forward_kinematics(
                                [math.radians(v) for v in target]
                            )
                        with self._socket_timeout(MOTION_TIMEOUT_S):
                            self._send_linear_pose(target_pose)
                        if self._query_collision_flag_locked():
                            raise RuntimeError(
                                "robot reported a collision during the linear move"
                            )
                        self._read_locked()
                        with self._state:
                            self.last_good_joints = (
                                list(self.joints_deg)
                                if self.joints_deg is not None
                                else list(target)
                            )
                    with self._state:
                        self.last_error = None
                except HTTPException as exc:
                    raise HTTPException(exc.status_code, f"pose '{name}': {exc.detail}") from exc
                except Exception as exc:
                    log.exception("linear pose move failed")
                    text = str(exc).lower()
                    collision = self._query_collision_flag_locked()
                    if collision or self._is_collision_text(text):
                        actual = self._read_after_fault_locked()
                        self._record_motion_fault_locked(exc, actual)
                        with self._state:
                            self.last_error = self.fault or f"{type(exc).__name__}: {exc}"
                        raise HTTPException(502, f"pose '{name}': {self.last_error}") from exc
                    if "goal has been aborted" in text:
                        log.warning(
                            "linear path for pose '%s' was aborted by the robot; "
                            "falling back to the joint-space path",
                            name,
                        )
                        try:
                            return self._move_locked(target, max_delta=MAX_POSE_DEG)
                        except HTTPException as fallback_exc:
                            raise HTTPException(
                                fallback_exc.status_code,
                                f"pose '{name}': {fallback_exc.detail}",
                            ) from fallback_exc
                    actual = self._read_after_fault_locked()
                    self._record_motion_fault_locked(exc, actual)
                    with self._state:
                        self.last_error = self.fault or f"{type(exc).__name__}: {exc}"
                        if type(exc).__name__ in CONNECTION_ERRORS:
                            self.connected = False
                    raise HTTPException(502, f"pose '{name}': {self.last_error}") from exc
                finally:
                    with self._state:
                        self.busy = False
                return self.snapshot()
            try:
                return self._move_locked(target, current, MAX_POSE_DEG)
            except HTTPException as exc:
                raise HTTPException(exc.status_code, f"pose '{name}': {exc.detail}") from exc

    # ---------- saved sequences ----------

    def _load_sequences_locked(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(SEQUENCES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        sequences = data.get("sequences", []) if isinstance(data, dict) else data
        if not isinstance(sequences, list):
            return []
        cleaned: list[dict[str, Any]] = []
        for entry in sequences:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            poses = entry.get("poses")
            dwell = entry.get("dwell_sec", 1.0)
            transit = entry.get("transit")
            if not isinstance(transit, str) or not transit:
                transit = None
            if (
                isinstance(name, str)
                and name
                and isinstance(poses, list)
                and 1 <= len(poses) <= 30
                and all(isinstance(p, str) and p for p in poses)
                and isinstance(dwell, (int, float))
                and math.isfinite(float(dwell))
            ):
                cleaned.append(
                    {
                        "name": name,
                        "poses": list(poses),
                        "dwell_sec": max(0.0, min(10.0, float(dwell))),
                        "transit": transit,
                        "created_at": entry.get("created_at"),
                        "updated_at": entry.get("updated_at"),
                    }
                )
        return cleaned

    def _write_sequences_locked(self, sequences: list[dict[str, Any]]) -> None:
        SEQUENCES_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SEQUENCES_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"sequences": sequences}, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(SEQUENCES_FILE)

    def list_sequences(self) -> dict[str, Any]:
        with self._sequences_lock:
            return {"sequences": self._load_sequences_locked()}

    def save_sequence(
        self, name: str, poses: list[str], dwell_sec: float, transit: str | None = None
    ) -> dict[str, Any]:
        name = name.strip()
        if not name or len(name) > 40 or "/" in name:
            raise HTTPException(400, "sequence name must be 1-40 characters without '/'")
        if not 1 <= len(poses) <= 30:
            raise HTTPException(400, "a sequence needs 1-30 steps")
        if any((not isinstance(p, str)) or not p for p in poses):
            raise HTTPException(400, "invalid pose name in sequence")
        if not math.isfinite(dwell_sec) or dwell_sec < 0 or dwell_sec > 10:
            raise HTTPException(400, "dwell_sec must be between 0 and 10")
        transit = transit.strip() if isinstance(transit, str) else None
        if not transit:
            transit = None
        with self._poses_lock:
            pose_names = {p["name"] for p in self._load_poses_locked()}
        missing = [p for p in poses if p not in pose_names]
        if transit is not None and transit != HOME_TRANSIT and transit not in pose_names:
            missing.append(transit)
        if missing:
            raise HTTPException(400, f"unknown pose(s): {', '.join(missing)}")
        now = datetime.now(timezone.utc).isoformat()
        with self._sequences_lock:
            sequences = self._load_sequences_locked()
            existing = next((s for s in sequences if s["name"] == name), None)
            if existing is not None:
                existing["poses"] = list(poses)
                existing["dwell_sec"] = dwell_sec
                existing["transit"] = transit
                existing["updated_at"] = now
                sequence = existing
            else:
                sequence = {
                    "name": name,
                    "poses": list(poses),
                    "dwell_sec": dwell_sec,
                    "transit": transit,
                    "created_at": now,
                    "updated_at": now,
                }
                sequences.append(sequence)
            self._write_sequences_locked(sequences)
        return {"sequence": sequence, "sequences": sequences}

    def delete_sequence(self, name: str) -> dict[str, Any]:
        with self._sequences_lock:
            sequences = self._load_sequences_locked()
            remaining = [s for s in sequences if s["name"] != name]
            if len(remaining) == len(sequences):
                raise HTTPException(404, f"sequence not found: {name}")
            self._write_sequences_locked(remaining)
        return {"sequences": remaining}


def _detect_qr_roi(
    image: Any,
    x: float,
    y: float,
    radius: int,
    roi: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    """Detect a QR code around a clicked pixel or inside a dragged ROI.

    The D455 RGB view is only 640x480 and the tag is small, so a full-frame
    detector is unreliable. The operator's click or box gives us a useful ROI;
    we upscale and threshold that ROI before asking OpenCV for the corners.
    """
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - depends on the runtime env
        raise HTTPException(503, "OpenCV is not installed on the arm server") from exc

    h, w = image.shape[:2]
    best: dict[str, Any] | None = None
    if roi is not None:
        rx, ry, rw, rh = roi
        boxes = [
            (
                max(0, int(rx)),
                max(0, int(ry)),
                min(w, int(rx + rw)),
                min(h, int(ry + rh)),
            )
        ]
    else:
        boxes = []
        for r in (radius, min(220, radius * 2)):
            boxes.append(
                (
                    max(0, int(x) - r),
                    max(0, int(y) - r),
                    min(w, int(x) + r),
                    min(h, int(y) + r),
                )
            )
    for x0, y0, x1, y1 in boxes:
        if x1 - x0 < 24 or y1 - y0 < 24:
            continue
        crop = image[y0:y1, x0:x1]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        for scale in (4, 6, 8):
            scaled = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            variants = (
                scaled,
                cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
                cv2.adaptiveThreshold(
                    scaled, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 5
                ),
            )
            for variant in variants:
                result = cv2.QRCodeDetector().detect(variant)
                if not result or not result[0] or result[1] is None:
                    continue
                points = np.asarray(result[1], dtype=np.float32).reshape(-1, 2)
                if len(points) != 4:
                    continue
                full = points / scale + np.array([x0, y0], dtype=np.float32)
                margin = 3.0
                if (
                    np.min(full[:, 0]) <= x0 + margin
                    or np.max(full[:, 0]) >= x1 - margin
                    or np.min(full[:, 1]) <= y0 + margin
                    or np.max(full[:, 1]) >= y1 - margin
                ):
                    # A candidate touching the ROI edge is usually a cage bar /
                    # arm edge, not the tag. Reject it instead of returning a
                    # plausible-looking false positive.
                    continue
                sides = np.linalg.norm(full - np.roll(full, 1, axis=0), axis=1)
                if float(sides.min()) < 8.0 or float(sides.max() / sides.min()) > 2.8:
                    continue
                area = 0.5 * abs(
                    float(
                        np.dot(full[:, 0], np.roll(full[:, 1], -1))
                        - np.dot(full[:, 1], np.roll(full[:, 0], -1))
                    )
                )
                if area < 200.0 or area > 6000.0:
                    continue
                center = full.mean(axis=0)
                angle = float(cv2.minAreaRect(full.astype(np.float32))[2])
                candidate = {
                    "ok": True,
                    "frame": {"w": w, "h": h},
                    "roi": {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0},
                    "center": {"x": round(float(center[0]), 2), "y": round(float(center[1]), 2)},
                    "corners": [
                        {"x": round(float(p[0]), 2), "y": round(float(p[1]), 2)}
                        for p in full
                    ],
                    "angle_deg": round(angle, 2),
                    "area_px": round(area, 1),
                }
                if best is None or candidate["area_px"] > best["area_px"]:
                    best = candidate
        if best is not None:
            break
    return best or {"ok": False, "frame": {"w": w, "h": h}, "error": "QR code not found near click"}


def _load_vision_calibration() -> dict[str, Any]:
    if not VISION_CALIBRATION_FILE.exists():
        return {"points": [], "homography": None, "rmse_m": None}
    try:
        data = json.loads(VISION_CALIBRATION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"points": [], "homography": None, "rmse_m": None}
    if not isinstance(data, dict):
        return {"points": [], "homography": None, "rmse_m": None}
    data.setdefault("points", [])
    data.setdefault("homography", None)
    data.setdefault("rmse_m", None)
    return data


def _save_vision_calibration(data: dict[str, Any]) -> None:
    VISION_CALIBRATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = VISION_CALIBRATION_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(VISION_CALIBRATION_FILE)


def _solve_vision_calibration(points: list[dict[str, float]]) -> tuple[list[list[float]], float]:
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(503, "OpenCV is not installed on the arm server") from exc
    if len(points) < 6:
        raise HTTPException(409, "at least six 2D-spread vision points are required")
    src = np.asarray([[p["px"], p["py"]] for p in points], dtype=np.float32)
    dst = np.asarray([[p["arm_x"], p["arm_y"]] for p in points], dtype=np.float32)
    px_span, py_span = np.ptp(src, axis=0)
    ax_span, ay_span = np.ptp(dst, axis=0)
    if px_span < 80 or py_span < 20 or ax_span < 0.04 or ay_span < 0.04:
        raise HTTPException(
            422,
            "calibration points are too collinear/concentrated; spread them in a 2D grid",
        )
    homography, _ = cv2.findHomography(src, dst, 0)
    if homography is None:
        raise HTTPException(422, "vision points are degenerate")
    projected = cv2.perspectiveTransform(src.reshape(-1, 1, 2), homography).reshape(-1, 2)
    rmse = float(np.sqrt(np.mean(np.sum((projected - dst) ** 2, axis=1))))
    return homography.tolist(), rmse


def _webctl_call(
    path: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: float = 8.0,
) -> Any:
    """Proxy the pull-mode S200 server (copied from the teammate repo)."""
    base = WHEELTEC_WEBCTL_URL.rstrip("/")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {"ok": True}
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            detail = payload.get("error") or payload.get("detail") or str(payload)
        except Exception:
            detail = str(exc)
        raise HTTPException(exc.code, detail) from exc
    except Exception as exc:
        raise HTTPException(502, f"vehicle backend unavailable: {exc}") from exc


def create_app(controller: ArmController) -> FastAPI:
    app = FastAPI(
        title="PackTug Arm Bridge",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "mock": controller.mock, "time": time.time()}

    def _proxy_camera(url: str, label: str):
        if not url:
            raise HTTPException(503, f"{label} URL is not configured")
        try:
            upstream = urllib.request.urlopen(url, timeout=10)
        except Exception as exc:
            raise HTTPException(502, f"{label} unavailable: {exc}") from exc

        content_type = (
            upstream.headers.get("Content-Type")
            or "multipart/x-mixed-replace; boundary=frame"
        )

        def generate():
            try:
                while True:
                    chunk = upstream.read(4096)
                    if not chunk:
                        break
                    yield chunk
            finally:
                upstream.close()

        return StreamingResponse(
            generate(),
            media_type=content_type,
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/camera/stream")
    def camera_stream():
        """Proxy the Jetson RGB MJPEG stream so the UI stays same-origin."""
        return _proxy_camera(CAMERA_STREAM_URL, "camera stream")

    @app.get("/api/camera/depth")
    def camera_depth():
        """Proxy the colorized Jetson depth MJPEG stream."""
        return _proxy_camera(CAMERA_DEPTH_URL, "camera depth stream")

    @app.get("/api/camera/depth_stats")
    def camera_depth_stats():
        if not CAMERA_DEPTH_STATS_URL:
            raise HTTPException(503, "CAMERA_DEPTH_STATS_URL is not configured")
        try:
            with urllib.request.urlopen(CAMERA_DEPTH_STATS_URL, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise HTTPException(502, f"camera depth stats unavailable: {exc}") from exc
        return payload

    @app.get("/api/camera/mode")
    def camera_mode(value: str):
        if value not in ("rgb", "depth"):
            raise HTTPException(400, "camera mode must be rgb or depth")
        if not CAMERA_MODE_URL:
            raise HTTPException(503, "CAMERA_MODE_URL is not configured")
        try:
            with urllib.request.urlopen(
                f"{CAMERA_MODE_URL}?value={value}", timeout=15
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise HTTPException(502, f"camera mode switch failed: {exc}") from exc
        if not payload.get("ok"):
            raise HTTPException(502, payload.get("error", "camera mode switch failed"))
        return payload

    @app.get("/api/vehicle")
    def vehicle_fleet() -> Any:
        return _webctl_call("/api/robot")

    @app.get("/api/vehicle/{robot_id}")
    def vehicle_robot(robot_id: str) -> Any:
        return _webctl_call(f"/api/robot/{urllib.parse.quote(robot_id, safe='')}")

    @app.get("/api/vehicle/{robot_id}/camera")
    def vehicle_camera(robot_id: str):
        suffix = f"/cam/{urllib.parse.quote(robot_id, safe='')}"
        return _proxy_camera(WHEELTEC_WEBCTL_URL.rstrip("/") + suffix, "vehicle camera")

    @app.post("/api/vision/qr")
    def vision_qr(body: QrIn) -> dict[str, Any]:
        """Detect the QR tag near a click in the current RGB snapshot."""
        try:
            with urllib.request.urlopen(CAMERA_SNAPSHOT_URL, timeout=6) as response:
                payload = response.read()
        except Exception as exc:
            raise HTTPException(502, f"camera snapshot unavailable: {exc}") from exc
        try:
            import cv2
        except ImportError as exc:  # pragma: no cover
            raise HTTPException(503, "OpenCV is not installed on the arm server") from exc
        image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(502, "camera snapshot was not a valid image")
        roi_values = (body.roi_x, body.roi_y, body.roi_w, body.roi_h)
        roi = None
        if any(value is not None for value in roi_values):
            if any(value is None for value in roi_values):
                raise HTTPException(400, "ROI needs x, y, w and h")
            rx, ry, rw, rh = (float(value) for value in roi_values if value is not None)
            if rw < 20 or rh < 20:
                raise HTTPException(400, "ROI must be at least 20x20 pixels")
            roi = (rx, ry, rw, rh)
        return _detect_qr_roi(image, body.x, body.y, body.radius, roi)

    @app.get("/api/vision/calibration")
    def vision_calibration() -> dict[str, Any]:
        return {**_load_vision_calibration(), "needed": 6}

    @app.post("/api/vision/calibration/point")
    def add_vision_calibration_point(body: VisionPointIn) -> dict[str, Any]:
        data = _load_vision_calibration()
        data["points"].append(body.model_dump())
        data["homography"] = None
        data["rmse_m"] = None
        _save_vision_calibration(data)
        return {**data, "needed": 6}

    @app.post("/api/vision/calibration/solve")
    def solve_vision_calibration() -> dict[str, Any]:
        data = _load_vision_calibration()
        homography, rmse = _solve_vision_calibration(data["points"])
        data["homography"] = homography
        data["rmse_m"] = round(rmse, 6)
        _save_vision_calibration(data)
        return {**data, "needed": 6}

    @app.delete("/api/vision/calibration")
    def reset_vision_calibration() -> dict[str, Any]:
        data = {"points": [], "homography": None, "rmse_m": None}
        _save_vision_calibration(data)
        return {**data, "needed": 6}

    @app.post("/api/vision/map")
    def map_vision_pixel(body: VisionPixelIn) -> dict[str, Any]:
        data = _load_vision_calibration()
        homography = data.get("homography")
        if not homography:
            raise HTTPException(409, "vision calibration is not solved")
        try:
            import cv2
        except ImportError as exc:  # pragma: no cover
            raise HTTPException(503, "OpenCV is not installed on the arm server") from exc
        matrix = np.asarray(homography, dtype=np.float64)
        pixel = np.asarray([[[body.x, body.y]]], dtype=np.float64)
        mapped = cv2.perspectiveTransform(pixel, matrix).reshape(-1)
        return {"ok": True, "x": round(float(mapped[0]), 6), "y": round(float(mapped[1]), 6)}

    @app.get("/api/arm/status")
    def arm_status() -> dict[str, Any]:
        return controller.status()

    @app.get("/api/arm/hardware")
    def arm_hardware() -> dict[str, Any]:
        return controller.hardware_status()

    @app.post("/api/arm/connect")
    def arm_connect(body: ConnectIn) -> dict[str, Any]:
        try:
            return controller.connect(body.ip.strip())
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(502, f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/arm/disconnect")
    def arm_disconnect() -> dict[str, Any]:
        return controller.disconnect()

    @app.post("/api/arm/jog")
    def arm_jog(body: JogIn) -> dict[str, Any]:
        return controller.jog(body.joint, body.delta_deg)

    @app.post("/api/arm/joints")
    def arm_joints(body: JointsIn) -> dict[str, Any]:
        return controller.move_joints(body.joints_deg)

    @app.post("/api/arm/joint")
    def arm_joint(body: JointIn) -> dict[str, Any]:
        return controller.move_joint(body.joint, body.angle_deg)

    @app.post("/api/arm/pose")
    def arm_pose(body: PoseIn) -> dict[str, Any]:
        return controller.move_pose(body)

    @app.post("/api/arm/motors/enable")
    def arm_enable_motors() -> dict[str, Any]:
        return controller.enable_motors()

    @app.post("/api/arm/velocity")
    def arm_velocity(body: VelocityIn) -> dict[str, Any]:
        return controller.set_velocity(body.percent)

    @app.post("/api/arm/learning")
    def arm_learning(body: LearningIn) -> dict[str, Any]:
        if body.enabled and not body.confirm:
            raise HTTPException(400, "confirmation required before releasing motors")
        return controller.set_learning(body.enabled)

    @app.post("/api/arm/gripper/open")
    def arm_gripper_open() -> dict[str, Any]:
        return controller.open_gripper()

    @app.post("/api/arm/gripper/close")
    def arm_gripper_close() -> dict[str, Any]:
        return controller.close_gripper()

    @app.post("/api/arm/gripper/raw")
    def arm_gripper_raw(body: GripperRawIn) -> dict[str, Any]:
        return controller.raw_gripper(body.open, body.speed)

    @app.post("/api/arm/tool/scan")
    def arm_tool_scan() -> dict[str, Any]:
        return controller.scan_tool()

    @app.post("/api/arm/home")
    def arm_home() -> dict[str, Any]:
        return controller.move_home()

    @app.post("/api/arm/recover")
    def arm_recover() -> dict[str, Any]:
        return controller.recover()

    @app.post("/api/arm/fault/clear")
    def arm_clear_fault() -> dict[str, Any]:
        return controller.clear_fault()

    @app.post("/api/arm/calibrate")
    def arm_calibrate(body: CalibrateIn) -> dict[str, Any]:
        if not body.confirm:
            raise HTTPException(400, "confirmation required before calibration")
        return controller.calibrate()

    @app.get("/api/arm/poses")
    def arm_poses() -> dict[str, Any]:
        return controller.list_poses()

    @app.post("/api/arm/poses")
    def arm_save_pose(body: SavePoseIn) -> dict[str, Any]:
        return controller.save_pose(body.name, body.joints_deg)

    @app.delete("/api/arm/poses/{name}")
    def arm_delete_pose(name: str) -> dict[str, Any]:
        return controller.delete_pose(name)

    @app.post("/api/arm/poses/{name}/apply")
    def arm_apply_pose(name: str, body: ApplyPoseIn | None = None) -> dict[str, Any]:
        return controller.apply_pose(name, True if body is None else body.linear)

    @app.post("/api/arm/reach")
    def arm_reach(body: ReachIn) -> dict[str, Any]:
        return controller.set_reach(body.max_reach_m)

    @app.get("/api/arm/sequences")
    def arm_sequences() -> dict[str, Any]:
        return controller.list_sequences()

    @app.post("/api/arm/sequences")
    def arm_save_sequence(body: SaveSequenceIn) -> dict[str, Any]:
        return controller.save_sequence(body.name, body.poses, body.dwell_sec, body.transit)

    @app.delete("/api/arm/sequences/{name}")
    def arm_delete_sequence(name: str) -> dict[str, Any]:
        return controller.delete_sequence(name)

    if (DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404)
        if path:
            target = (DIST / path).resolve()
            try:
                target.relative_to(DIST.resolve())
            except ValueError:
                raise HTTPException(404)
            if target.is_file():
                return FileResponse(target)
        index = DIST / "index.html"
        if index.is_file():
            return FileResponse(index)
        raise HTTPException(404, "frontend not built — run npm run build")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Ned1 arm bridge + control-panel server")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP listen address (use 0.0.0.0 to expose on LAN)")
    parser.add_argument("--port", type=int, default=8020, help="HTTP listen port")
    parser.add_argument("--arm-host", default=DEFAULT_HOST, help="Ned1 IP (10.10.10.10 WiFi / 169.254.200.200 Ethernet)")
    parser.add_argument("--mock", action="store_true", help="simulate the arm (no hardware)")
    parser.add_argument("--auto-connect", action="store_true", help="connect to the arm at startup")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    controller = ArmController(mock=args.mock)
    if args.auto_connect:
        try:
            controller.connect(args.arm_host)
        except Exception as exc:
            log.error("initial connect to %s failed: %s", args.arm_host, exc)

    import uvicorn

    uvicorn.run(create_app(controller), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
