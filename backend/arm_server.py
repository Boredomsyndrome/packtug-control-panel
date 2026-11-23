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

    conda run --no-capture-output -n ned python backend/arm_server.py --port 8020
    conda run --no-capture-output -n ned python backend/arm_server.py --mock
"""

from __future__ import annotations

import argparse
import base64
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
    "http://127.0.0.1:8080/stream",
)
CAMERA_DEPTH_URL = os.getenv(
    "CAMERA_DEPTH_URL",
    "http://127.0.0.1:8080/depth",
)
CAMERA_DEPTH_STATS_URL = os.getenv(
    "CAMERA_DEPTH_STATS_URL",
    "http://127.0.0.1:8080/depth_stats",
)
CAMERA_MODE_URL = os.getenv(
    "CAMERA_MODE_URL",
    "http://127.0.0.1:8080/mode",
)
CAMERA_SNAPSHOT_URL = os.getenv(
    "CAMERA_SNAPSHOT_URL",
    CAMERA_STREAM_URL.rsplit("/", 1)[0] + "/snapshot",
)
CAMERA_INFO_URL = os.getenv(
    "CAMERA_INFO_URL",
    CAMERA_STREAM_URL.rsplit("/", 1)[0] + "/camera_info",
)
WHEELTEC_WEBCTL_URL = os.getenv("WHEELTEC_WEBCTL_URL", "http://127.0.0.1:8090")
MAX_STEP_DEG = 60.0       # internal segment size; one segment per manual command
MAX_REQUEST_DEG = 60.0    # largest joint change accepted per manual command
MAX_POSE_DEG = 180.0      # largest joint change when recalling a saved pose
MIN_POSE_Z_M = 0.02       # lowest tool z accepted for a Cartesian target
MAX_POSE_Z_M = 0.50       # highest tool z accepted for a Cartesian target
MAX_VELOCITY = 20
MAX_VELOCITY_LIMIT = 20
POSES_FILE = Path(__file__).resolve().parent / "arm_poses.json"
SEQUENCES_FILE = Path(__file__).resolve().parent / "arm_sequences.json"
VISION_CALIBRATION_FILE = Path(__file__).resolve().parent / "vision_calibration.json"
HAND_EYE_CALIBRATION_FILE = Path(__file__).resolve().parent / "hand_eye_calibration.json"
CAMERA_INTRINSICS_FILE = Path(__file__).resolve().parent / "camera_intrinsics.json"
QR_CODE_SIZE_M = float(os.getenv("QR_CODE_SIZE_M", "0.035"))
CHARUCO_SQUARES_X = 5
CHARUCO_SQUARES_Y = 7
CHARUCO_SQUARE_M = 0.030
CHARUCO_MARKER_M = 0.022
HOME_TRANSIT = "__home__"   # built-in safe transit: pyniryo move_to_home_pose()
SEQUENCE_GRIPPER_OPEN = "__gripper_open__"
SEQUENCE_GRIPPER_CLOSE = "__gripper_close__"
SEQUENCE_ACTION_STEPS = {SEQUENCE_GRIPPER_OPEN, SEQUENCE_GRIPPER_CLOSE}
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
MOTION_FAULT_ENABLED = os.getenv("ARM_MOTION_FAULT_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
COLLISION_MARKERS = (
    "collision",
    "overload",
    "torque",
    "blocked",
    "hardware error",
    "motor error",
)
HARDWARE_REFRESH_S = 2.0
HARDWARE_ERROR_BITS = (
    (0x20, "overload"),
    (0x10, "electrical shock"),
    (0x08, "encoder"),
    (0x04, "overheating"),
    (0x01, "input voltage"),
)


def decode_hardware_faults(
    raw_errors: Any, motor_names: list[str] | None = None
) -> tuple[list[dict[str, Any]], str | None]:
    """Convert PyNiryo's per-motor hardware error codes into readable faults."""
    if raw_errors is None:
        raw_errors = []
    elif isinstance(raw_errors, (str, bytes)):
        raw_errors = [raw_errors]
    else:
        raw_errors = list(raw_errors)
    names = [str(name) for name in (motor_names or [])]
    faults: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_errors):
        text = str(raw).strip()
        if text.lower() in ("", "0", "none", "null"):
            continue
        try:
            code: int | str = int(raw)
        except (TypeError, ValueError):
            code = text
        reasons = (
            [label for bit, label in HARDWARE_ERROR_BITS if code & bit]
            if isinstance(code, int)
            else [text]
        )
        if not reasons:
            reasons = [f"error {code}"]
        if index < 6:
            joint = f"J{index + 1}"
        elif index < len(names) and names[index]:
            joint = names[index]
        else:
            joint = f"motor {index + 1}"
        message = f"{joint} {', '.join(reasons)} (code {code})"
        faults.append({
            "index": index,
            "joint": joint,
            "name": names[index] if index < len(names) else joint,
            "code": code,
            "reasons": reasons,
            "message": message,
        })
    return faults, "; ".join(fault["message"] for fault in faults) if faults else None


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


class VisionPoseIn(BaseModel):
    x: float
    y: float
    z: float
    roll: float
    pitch: float
    yaw: float


class QrIn(BaseModel):
    x: float
    y: float
    radius: int = Field(default=60, ge=40, le=160)
    roi_x: float | None = None
    roi_y: float | None = None
    roi_w: float | None = None
    roi_h: float | None = None
    image_data_url: str | None = None
    arm_pose: VisionPoseIn | None = None
    captured_at: float | None = None
    target_offset_x_m: float = Field(default=0.0, ge=-0.25, le=0.25)
    target_offset_y_m: float = Field(default=0.0525, ge=-0.25, le=0.25)
    target_offset_z_m: float = Field(default=0.0, ge=-0.25, le=0.25)


class HandEyeCaptureIn(BaseModel):
    image_data_url: str
    arm_pose: VisionPoseIn
    captured_at: float | None = None


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


class JogStreamIn(BaseModel):
    """Normalized real-time velocity for the continuous jog thread."""
    x: float = Field(default=0.0, ge=-1.0, le=1.0)
    y: float = Field(default=0.0, ge=-1.0, le=1.0)
    z: float = Field(default=0.0, ge=-1.0, le=1.0)
    roll: float = Field(default=0.0, ge=-1.0, le=1.0)
    pitch: float = Field(default=0.0, ge=-1.0, le=1.0)
    yaw: float = Field(default=0.0, ge=-1.0, le=1.0)
    j6: float = Field(default=0.0, ge=-1.0, le=1.0)
    scale: float = Field(default=1.0, ge=1.0, le=6.0)


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


class LinearMoveIn(BaseModel):
    """Small Cartesian jog; roll/pitch stay locked, yaw may be offset."""
    dx: float = Field(default=0.0, ge=-0.25, le=0.25)
    dy: float = Field(default=0.0, ge=-0.25, le=0.25)
    dz: float = Field(default=0.0, ge=-0.25, le=0.25)
    dpitch: float = Field(default=0.0, ge=-90.0, le=90.0)
    dyaw: float = Field(default=0.0, ge=-90.0, le=90.0)
    dj6: float = Field(default=0.0, ge=-30.0, le=30.0)
    droll: float = Field(default=0.0, ge=-90.0, le=90.0)


class Vec3In(BaseModel):
    x: float
    y: float
    z: float


class CageIn(BaseModel):
    """Cage pose in the arm base frame plus the outer box dimensions.

    The local cage frame is centred on the opening: X = width (-W/2..+W/2),
    Y = depth (0..D, +Y into the cage) and Z = height (0..H). The opening is
    the Y = 0 face, so the arm enters along +Y. ``front_top_open`` drops the
    front-top edge (the real cage has no bar there).
    """

    x: float = 0.35
    y: float = 0.0
    z: float = 0.0
    yaw: float = 0.0
    width: float = Field(default=0.55, gt=0.05, le=2.0)
    depth: float = Field(default=0.25, gt=0.05, le=2.0)
    height: float = Field(default=0.40, gt=0.05, le=2.0)
    opening_margin: float = Field(default=0.02, ge=0.0, le=0.2)
    clearance_m: float = Field(default=0.03, ge=0.005, le=0.2)
    front_top_open: bool = True


class PickPathIn(BaseModel):
    """Dry-run Cartesian pick path request."""

    start: PoseIn | None = None
    preserve_yaw: bool = False
    via: PoseIn | None = None
    home: PoseIn | None = None
    target: PoseIn
    approach_m: float = Field(default=0.06, ge=0.0, le=0.25)
    gate_clearance_m: float = Field(default=0.08, ge=0.02, le=0.30)
    # Radius of the carried carton/gripper envelope. The path planner inflates
    # cage clearance by this amount so the swept payload does not clip a frame.
    payload_radius_m: float = Field(default=0.0, ge=0.0, le=0.25)
    cage: CageIn = Field(default_factory=CageIn)
    # Eight taught corners; when present they override the box pose/size.
    corners: list[Vec3In] | None = None
    # Corner indices to ignore (currently the two back-top corners, 6 and 7).
    ignored_corners: list[int] = Field(default_factory=list)


class CageFitIn(BaseModel):
    points: list[Vec3In]
    opening_margin: float = Field(default=0.02, ge=0.0, le=0.2)
    clearance_m: float = Field(default=0.03, ge=0.005, le=0.2)
    front_top_open: bool = True
    ignored_corners: list[int] = Field(default_factory=list)


class ExecutePickIn(BaseModel):
    """Blocking pick/place: close, pull back, lower, safe-home, place, open."""

    start: PoseIn | None = None
    target: PoseIn
    home: PoseIn | None = None
    home_lift_m: float = Field(default=0.08, ge=0.0, le=0.20)
    retract_m: float = Field(default=0.08, ge=0.0, le=0.20)
    fold_j2_to_min: bool = False
    approach_m: float = Field(default=0.05, ge=0.0, le=0.15)
    gate_clearance_m: float = Field(default=0.08, ge=0.02, le=0.30)
    payload_radius_m: float = Field(default=0.0, ge=0.0, le=0.25)
    cage: CageIn = Field(default_factory=CageIn)
    corners: list[Vec3In] | None = None
    ignored_corners: list[int] = Field(default_factory=list)
    speed_percent: int = Field(default=5, ge=1, le=MAX_VELOCITY_LIMIT)
    return_home_after: bool = False


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
        self.jog_control_enabled: bool = False
        self._stop_requested = threading.Event()
        self._jog_stream_lock = threading.Lock()
        self._jog_stream_stop = threading.Event()
        self._jog_stream_thread: threading.Thread | None = None
        self._jog_stream_velocity: dict[str, float] | None = None
        self._jog_stream_scale = 1.0
        self._jog_stream_last_update = 0.0
        self._jog_stream_target: dict[str, float] | None = None
        self._jog_stream_base_j6: float | None = None
        self._jog_stream_j6_offset = 0.0
        self.hardware_errors: list[Any] = []
        self.hardware_faults: list[dict[str, Any]] = []
        self.hardware_fault: str | None = None
        self.hardware_motion_fault: str | None = None
        self.hardware_tool_fault: str | None = None
        self.hardware_details: dict[str, Any] = {}
        self._hardware_checked_at = 0.0

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
                "hardware_errors": list(self.hardware_errors),
                "hardware_fault": self.hardware_fault,
                "hardware_motion_fault": self.hardware_motion_fault,
                "hardware_tool_fault": self.hardware_tool_fault,
                "hardware_faults": [dict(fault) for fault in self.hardware_faults],
                "motors_temperature": list(
                    self.hardware_details.get("motors_temperature", [])
                ),
                "motors_voltage": list(self.hardware_details.get("motors_voltage", [])),
                "motor_names": list(self.hardware_details.get("motor_names", [])),
                "updated_at": self.updated_at,
            }

    def _store_hardware_locked(self, hw: Any) -> None:
        names = [str(name) for name in (getattr(hw, "motor_names", []) or [])]
        raw_errors = getattr(hw, "hardware_errors", []) or []
        if isinstance(raw_errors, (str, bytes)):
            raw_errors = [raw_errors]
        raw_errors = list(raw_errors)
        faults, summary = decode_hardware_faults(raw_errors, names)
        message = str(getattr(hw, "error_message", "") or "").strip()
        if len(message) >= 2 and message[0] == message[-1] and message[0] in "'\"":
            message = message[1:-1].strip()
        if message.lower() in ("", "none", "null", "ok", "0", "no error", "no hardware error"):
            message = ""
        # The robot uses "''" as its no-error sentinel. Keep a real
        # error_message as a fallback when no per-motor code is reported.
        if not summary and message:
            summary = message
        motion_fault = "; ".join(
            fault["message"] for fault in faults if int(fault["index"]) < 6
        ) or None
        tool_fault = "; ".join(
            fault["message"] for fault in faults if int(fault["index"]) >= 6
        ) or None
        if not faults and message:
            # Robot-level error messages are treated as motion faults; the
            # tool has its own per-motor code when the gripper is the problem.
            motion_fault = message
        details = {
            "hardware_version": getattr(hw, "hardware_version", None),
            "rpi_temperature": getattr(hw, "rpi_temperature", None),
            "connection_up": getattr(hw, "connection_up", None),
            "error_message": message or None,
            "calibration_needed": getattr(hw, "calibration_needed", None),
            "calibration_in_progress": getattr(hw, "calibration_in_progress", None),
            "motor_names": names,
            "motor_types": list(getattr(hw, "motor_types", []) or []),
            "motors_temperature": list(getattr(hw, "motors_temperature", []) or []),
            "motors_voltage": list(getattr(hw, "motors_voltage", []) or []),
            "hardware_errors": raw_errors,
        }
        with self._state:
            self.hardware_errors = raw_errors
            self.hardware_faults = faults
            self.hardware_fault = summary
            self.hardware_motion_fault = motion_fault
            self.hardware_tool_fault = tool_fault
            self.hardware_details = details
            self._hardware_checked_at = time.monotonic()

    def _refresh_hardware_locked(self, force: bool = False) -> None:
        """Refresh the cached motor error state (at most every HARDWARE_REFRESH_S)."""
        if self.mock or self._robot is None:
            return
        with self._state:
            if not force and time.monotonic() - self._hardware_checked_at < HARDWARE_REFRESH_S:
                return
        try:
            with self._socket_timeout(READ_TIMEOUT_S):
                hw = self._robot.get_hardware_status()
        except Exception as exc:
            log.debug("hardware status read failed: %s", exc)
            with self._state:
                self._hardware_checked_at = time.monotonic()
            return
        self._store_hardware_locked(hw)

    def hardware_status(self) -> dict[str, Any]:
        with self._motion:
            with self._state:
                if not self.connected:
                    raise HTTPException(409, "arm is not connected")
                if not self.mock and self._robot is None:
                    raise HTTPException(409, "arm is not connected")
            if self.mock:
                return {
                    "mock": True,
                    "hardware_version": "mock",
                    "error_message": None,
                    "hardware_errors": [],
                    "hardware_fault": None,
                    "hardware_motion_fault": None,
                    "hardware_tool_fault": None,
                    "hardware_faults": [],
                    "motors_temperature": [],
                    "motors_voltage": [],
                    "motor_names": [],
                }
            self._refresh_hardware_locked(force=True)
            with self._state:
                return {
                    "mock": False,
                    **self.hardware_details,
                    "hardware_fault": self.hardware_fault,
                    "hardware_motion_fault": self.hardware_motion_fault,
                    "hardware_tool_fault": self.hardware_tool_fault,
                    "hardware_faults": [dict(fault) for fault in self.hardware_faults],
                }

    def status(self) -> dict[str, Any]:
        if self.connected and not self.mock and not self.busy:
            if self._motion.acquire(blocking=False):
                try:
                    self._read_locked()
                    self._refresh_hardware_locked()
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
                    self.jog_control_enabled = False
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
                self._refresh_hardware_locked(force=True)
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
            self.jog_control_enabled = False
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
            self.hardware_errors = []
            self.hardware_faults = []
            self.hardware_fault = None
            self.hardware_motion_fault = None
            self.hardware_tool_fault = None
            self.hardware_details = {}
            self._hardware_checked_at = 0.0
        self._stop_jog_stream()

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
        self._refresh_hardware_locked(force=True)
        with self._state:
            return self.hardware_fault

    @staticmethod
    def _is_collision_text(text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in COLLISION_MARKERS)

    def _record_motion_fault_locked(self, exc: BaseException, actual: list[float] | None = None) -> None:
        """Record a failed move; latching is temporarily disabled for demos."""
        text = str(exc).strip() or type(exc).__name__
        detected = self._query_collision_flag_locked()
        collision = bool(detected) or self._is_collision_text(text)
        hardware = self._hardware_fault_message_locked()
        if hardware and hardware.lower() not in text.lower():
            text = f"{text} (hardware: {hardware})"
        with self._state:
            if actual is not None:
                self.joints_deg = [float(v) for v in actual]
            self.last_error = text
            self.updated_at = time.time()
            if MOTION_FAULT_ENABLED:
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
            else:
                self.fault = None
                self.collision = False
                self.recovery_available = False
                self.recovery_target_deg = None
        if detected:
            self._clear_collision_flag_locked()
        if MOTION_FAULT_ENABLED:
            log.error("arm motion fault%s: %s", " (collision)" if collision else "", text)
        else:
            log.warning("arm motion error (fault latch frozen): %s", text)

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

    def _disable_jog_locked(self) -> None:
        if not self.jog_control_enabled:
            return
        try:
            if self._robot is not None:
                with self._socket_timeout(TOOL_TIMEOUT_S):
                    self._robot.set_jog_control(False)
        except Exception as exc:
            log.warning("could not disable jog control: %s", exc)
        finally:
            self.jog_control_enabled = False

    def _check_motion_locked(self, allow_jog: bool = False) -> None:
        with self._state:
            if not self.connected:
                raise HTTPException(409, "arm is not connected")
            if not self.mock and self._robot is None:
                raise HTTPException(409, "arm is not connected")
            if not allow_jog:
                self._stop_jog_stream()
                self._disable_jog_locked()
        self._refresh_hardware_locked()
        with self._state:
            if self.calibration_needed:
                raise HTTPException(409, "calibration required — run ned_calibrate.py --go")
            if self.learning_mode:
                raise HTTPException(409, "motors are released — enable motors first")
            hardware_fault = self.hardware_motion_fault
            if MOTION_FAULT_ENABLED and self.fault:
                raise HTTPException(
                    409,
                    "arm is in a motion fault state — press Recover to retract to the "
                    "last safe point before moving again",
                )
        if hardware_fault:
            raise HTTPException(
                409,
                f"hardware fault: {hardware_fault}. Clear the obstruction/load, "
                "power-cycle the arm, and reconnect before moving.",
            )

    def _check_tool_locked(self) -> None:
        with self._state:
            if not self.connected:
                raise HTTPException(409, "arm is not connected")
        self._disable_jog_locked()
        with self._state:
            if self.learning_mode:
                raise HTTPException(409, "motors are released — enable motors first")
            if MOTION_FAULT_ENABLED and self.fault:
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
            # A calibration offset can leave an untouched joint a few
            # hundredths of a degree outside the soft limit. Do not block
            # moving a different joint just because that unchanged joint is
            # already at the boundary; only reject a target that moves it
            # farther out or a changed target outside the range.
            if abs(value - current[i]) <= 1e-6:
                continue
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

    def move_linear(
        self, dx: float, dy: float, dz: float, dyaw: float = 0.0, dpitch: float = 0.0
    ) -> dict[str, Any]:
        """Move by a Cartesian delta while preserving the current tool attitude.

        PyNiryo's linear pose command interpolates the TCP along a straight
        line. Roll/pitch stay fixed; yaw can be offset separately. The wrist
        joints compensate so the gripper remains horizontal during the move.
        """
        values = (dx, dy, dz, dyaw, dpitch)
        if not all(math.isfinite(value) for value in values):
            raise HTTPException(400, "linear deltas must be finite")
        if not any(abs(value) > 1e-9 for value in values):
            return self.snapshot()
        with self._motion:
            self._check_motion_locked()
            current = self.snapshot().get("pose")
        if current is None:
            raise HTTPException(502, "current pose is unavailable")
        target_x = float(current["x"]) + float(dx)
        target_y = float(current["y"]) + float(dy)
        target_z = float(current["z"]) + float(dz)
        target_pitch = (float(current["pitch"]) + float(dpitch) + 180.0) % 360.0 - 180.0
        target_yaw = (float(current["yaw"]) + float(dyaw) + 180.0) % 360.0 - 180.0
        return self.move_pose_deg(
            target_x,
            target_y,
            target_z,
            float(current["roll"]),
            target_pitch,
            target_yaw,
            strict_linear=True,
        )

    def jog_pose(
        self,
        dx: float,
        dy: float,
        dz: float,
        dpitch: float = 0.0,
        dyaw: float = 0.0,
        dj6: float = 0.0,
        droll: float = 0.0,
    ) -> dict[str, Any]:
        """Stream a small orientation-locked Cartesian offset via JOG_JOINTS.

        The firmware's JOG_POSE command was changing wrist orientation during
        pure translation on this Ned1. Solve IK for the target pose first and
        send only the small joint deltas through JOG_JOINTS; roll is kept at
        the current value, pitch/yaw are optional offsets, and J6 can be
        adjusted separately with ``dj6``.
        """
        values = (dx, dy, dz, dpitch, dyaw, dj6, droll)
        if not all(math.isfinite(value) for value in values):
            raise HTTPException(400, "jog deltas must be finite")
        if abs(dpitch) < 0.05:
            dpitch = 0.0
        if abs(dyaw) < 0.05:
            dyaw = 0.0
        if abs(droll) < 0.05:
            droll = 0.0
        if not any(abs(value) > 1e-9 for value in values):
            return self.snapshot()
        log.info(
            "jog dx=%.4f dy=%.4f dz=%.4f droll=%.2f dpitch=%.2f dyaw=%.2f dj6=%.2f",
            dx, dy, dz, droll, dpitch, dyaw, dj6,
        )
        with self._motion:
            self._check_motion_locked(allow_jog=True)
            with self._state:
                current_pose = dict(self.pose) if self.pose is not None else None
                current_joints = (
                    list(self.joints_deg) if self.joints_deg is not None else None
                )
            if current_pose is None or current_joints is None:
                raise HTTPException(502, "current pose or joints are unavailable")
            target_x = float(current_pose["x"]) + float(dx)
            target_y = float(current_pose["y"]) + float(dy)
            target_z = float(current_pose["z"]) + float(dz)
            target_roll = (float(current_pose["roll"]) + float(droll) + 180.0) % 360.0 - 180.0
            target_pitch = (float(current_pose["pitch"]) + float(dpitch) + 180.0) % 360.0 - 180.0
            target_yaw = (float(current_pose["yaw"]) + float(dyaw) + 180.0) % 360.0 - 180.0
            if math.hypot(target_x, target_y) > self.max_reach_m + 1e-6:
                raise HTTPException(400, "jog would exceed max horizontal reach")
            if not (MIN_POSE_Z_M <= target_z <= MAX_POSE_Z_M):
                raise HTTPException(
                    400,
                    f"jog would put z outside [{MIN_POSE_Z_M:.2f}, {MAX_POSE_Z_M:.2f}] m",
                )
            if self.mock:
                with self._state:
                    self.pose = {
                        **current_pose,
                        "x": target_x,
                        "y": target_y,
                        "z": target_z,
                        "roll": target_roll,
                        "pitch": target_pitch,
                        "yaw": target_yaw,
                    }
                    self.joints_deg = list(current_joints)
                    self.joints_deg[5] = (
                        self.joints_deg[5] + float(dj6) + 180.0
                    ) % 360.0 - 180.0
                    self.updated_at = time.time()
                    self.last_error = None
                return self.snapshot()
            try:
                with self._socket_timeout(READ_TIMEOUT_S):
                    solved = self._robot.inverse_kinematics(
                        [
                            target_x,
                            target_y,
                            target_z,
                            math.radians(target_roll),
                            math.radians(target_pitch),
                            math.radians(target_yaw),
                        ]
                    )
                target_joints = [math.degrees(float(v)) for v in solved]
                if len(target_joints) != 6:
                    raise RuntimeError("IK returned an invalid joint solution")
                target_joints[5] += float(dj6)
                target_joints[5] = (target_joints[5] + 180.0) % 360.0 - 180.0
                delta = [b - a for a, b in zip(current_joints, target_joints)]
                limit = 8.0
                largest = max(abs(value) for value in delta)
                if largest > limit:
                    scale = limit / largest
                    delta = [value * scale for value in delta]
                next_joints = [a + b for a, b in zip(current_joints, delta)]
                for index, value in enumerate(next_joints):
                    lo, hi = JOINT_LIMITS_DEG[index]
                    if value < lo - 1e-6 or value > hi + 1e-6:
                        raise HTTPException(
                            400,
                            f"jog would push J{index + 1} outside its joint limit",
                        )
                if not self.jog_control_enabled:
                    with self._socket_timeout(TOOL_TIMEOUT_S):
                        self._robot.set_jog_control(True)
                    with self._state:
                        self.jog_control_enabled = True
                with self._socket_timeout(TOOL_TIMEOUT_S):
                    self._robot.jog_joints([math.radians(value) for value in delta])
                if self._query_collision_flag_locked():
                    raise RuntimeError("robot reported a collision during jog")
                # Keep the local target in sync without an extra TCP round-trip
                # on every 120 ms tick; the 1 Hz status poll refreshes the
                # authoritative pose. This is what keeps jogging responsive.
                with self._state:
                    self.joints_deg = next_joints
                    self.pose = {
                        **current_pose,
                        "x": target_x,
                        "y": target_y,
                        "z": target_z,
                        "roll": target_roll,
                        "pitch": target_pitch,
                        "yaw": target_yaw,
                    }
                    self.last_error = None
                    self.updated_at = time.time()
            except HTTPException:
                raise
            except Exception as exc:
                actual = self._read_after_fault_locked()
                self._record_motion_fault_locked(exc, actual)
                with self._state:
                    self.last_error = self.fault or f"{type(exc).__name__}: {exc}"
                raise HTTPException(502, self.last_error) from exc
        return self.snapshot()

    def update_jog_stream(
        self,
        x: float,
        y: float,
        z: float,
        roll: float,
        pitch: float,
        yaw: float,
        j6: float,
        scale: float,
    ) -> dict[str, Any]:
        """Set/update the normalized velocity consumed by the jog thread."""
        velocity = {
            "x": float(x), "y": float(y), "z": float(z),
            "roll": float(roll), "pitch": float(pitch), "yaw": float(yaw),
            "j6": float(j6),
        }
        moving = any(abs(value) > 1e-9 for value in velocity.values())
        if not moving:
            with self._jog_stream_lock:
                self._jog_stream_last_update = time.monotonic()
                self._jog_stream_scale = float(scale)
                self._jog_stream_velocity = None
                self._jog_stream_target = None
                self._jog_stream_base_j6 = None
                self._jog_stream_j6_offset = 0.0
                self._jog_stream_stop.set()
            return self.snapshot()
        with self._state:
            initial_pose = dict(self.pose) if self.pose is not None else None
            initial_j6 = float(self.joints_deg[5]) if self.joints_deg is not None else None
        with self._jog_stream_lock:
            self._jog_stream_last_update = time.monotonic()
            self._jog_stream_scale = float(scale)
            self._jog_stream_velocity = velocity
            if self._jog_stream_target is None and initial_pose is not None:
                self._jog_stream_target = initial_pose
                self._jog_stream_base_j6 = initial_j6
                self._jog_stream_j6_offset = 0.0
            self._jog_stream_stop.clear()
            if self._jog_stream_thread is None or not self._jog_stream_thread.is_alive():
                self._jog_stream_thread = threading.Thread(
                    target=self._jog_stream_loop,
                    daemon=True,
                    name="arm-jog-stream",
                )
                self._jog_stream_thread.start()
        return self.snapshot()

    def stop_motion(self) -> dict[str, Any]:
        """Operator stop: cancel jog and abort the next automatic path step."""
        self._stop_requested.set()
        self._stop_jog_stream()
        with self._state:
            self.busy = False
            self.last_error = "motion stopped by operator"
            self.updated_at = time.time()
        return self.snapshot()

    def stop_jog_stream(self) -> dict[str, Any]:
        self._stop_jog_stream()
        return self.snapshot()

    def _stop_jog_stream(self) -> None:
        with self._jog_stream_lock:
            self._jog_stream_velocity = None
            self._jog_stream_target = None
            self._jog_stream_base_j6 = None
            self._jog_stream_j6_offset = 0.0
            self._jog_stream_stop.set()

    def _jog_stream_loop(self) -> None:
        try:
            while not self._jog_stream_stop.is_set():
                with self._jog_stream_lock:
                    velocity = (
                        dict(self._jog_stream_velocity)
                        if self._jog_stream_velocity is not None
                        else None
                    )
                    scale = self._jog_stream_scale
                    last_update = self._jog_stream_last_update
                    target = (
                        dict(self._jog_stream_target)
                        if self._jog_stream_target is not None
                        else None
                    )
                    j6_offset = self._jog_stream_j6_offset
                    base_j6 = self._jog_stream_base_j6
                if velocity is None:
                    time.sleep(0.02)
                    continue
                if time.monotonic() - last_update > 0.8:
                    self._stop_jog_stream()
                    break
                if not self.connected:
                    time.sleep(0.05)
                    continue
                with self._motion:
                    try:
                        self._check_motion_locked(allow_jog=True)
                        with self._state:
                            current_joints = (
                                list(self.joints_deg)
                                if self.joints_deg is not None
                                else None
                            )
                            if target is None and self.pose is not None:
                                target = dict(self.pose)
                        if target is None or current_joints is None:
                            target = None
                            time.sleep(0.02)
                            continue
                        target = self._advance_jog_target(target, velocity, scale)
                        if target is None:
                            self._stop_jog_stream()
                            break
                        if self.mock:
                            with self._state:
                                self.pose = dict(target)
                                self.updated_at = time.time()
                                self.last_error = None
                            with self._jog_stream_lock:
                                self._jog_stream_target = dict(target)
                            time.sleep(0.02)
                            continue
                        # Use the robot's native Cartesian planner with an
                        # absolute orientation target. Do not solve IK locally:
                        # J4/J6 must be allowed to compensate together so the
                        # tool orientation stays consistent.
                        self._disable_jog_locked()
                        target_pose = SimpleNamespace(
                            x=target["x"],
                            y=target["y"],
                            z=target["z"],
                            roll=math.radians(target["roll"]),
                            pitch=math.radians(target["pitch"]),
                            yaw=math.radians(target["yaw"]),
                        )
                        with self._socket_timeout(MOTION_TIMEOUT_S):
                            self._send_linear_pose(target_pose)
                        if self._query_collision_flag_locked():
                            raise RuntimeError("robot reported a collision during jog")
                        with self._state:
                            self.pose = dict(target)
                            self.last_error = None
                            self.updated_at = time.time()
                        with self._jog_stream_lock:
                            self._jog_stream_target = dict(target)
                    except Exception as exc:
                        log.warning("jog stream error: %s", exc)
                        with self._state:
                            self.last_error = f"{type(exc).__name__}: {exc}"
                        with self._jog_stream_lock:
                            self._jog_stream_velocity = None
                            self._jog_stream_stop.set()
                        time.sleep(0.08)
                        break
                time.sleep(0.01)
        finally:
            with self._jog_stream_lock:
                self._jog_stream_thread = None
                self._jog_stream_velocity = None
                self._jog_stream_target = None
                self._jog_stream_base_j6 = None
                self._jog_stream_j6_offset = 0.0

    def _advance_jog_target(
        self, target: dict[str, float], velocity: dict[str, float], scale: float
    ) -> dict[str, float] | None:
        next_target = dict(target)
        next_target["x"] += velocity["x"] * 0.006 * scale
        next_target["y"] += velocity["y"] * 0.006 * scale
        next_target["z"] += velocity["z"] * 0.006 * scale
        next_target["roll"] = (
            next_target["roll"]
            + (velocity["roll"] + velocity["j6"]) * 2.0 * scale
            + 180.0
        ) % 360.0 - 180.0
        next_target["pitch"] = (
            next_target["pitch"] + velocity["pitch"] * 2.0 * scale + 180.0
        ) % 360.0 - 180.0
        next_target["yaw"] = (
            next_target["yaw"] + velocity["yaw"] * 2.0 * scale + 180.0
        ) % 360.0 - 180.0
        if math.hypot(next_target["x"], next_target["y"]) > self.max_reach_m + 1e-6:
            return None
        if not (MIN_POSE_Z_M <= next_target["z"] <= MAX_POSE_Z_M):
            return None
        return next_target

    def move_pose_deg(
        self,
        x: float,
        y: float,
        z: float,
        roll_deg: float,
        pitch_deg: float,
        yaw_deg: float,
        *,
        strict_linear: bool = False,
    ) -> dict[str, Any]:
        """Linear Cartesian move with degrees converted to radians.

        ``move_pose`` above is inherited from upstream and sends degrees raw;
        the pick/place executor uses this correctly-converted variant.
        """
        values = (x, y, z, roll_deg, pitch_deg, yaw_deg)
        if not all(math.isfinite(v) for v in values):
            raise HTTPException(400, "pose values must be finite")
        if math.hypot(x, y) > self.max_reach_m + 1e-6:
            raise HTTPException(
                400,
                f"horizontal reach {math.hypot(x, y):.3f} m exceeds max "
                f"{self.max_reach_m:.3f} m",
            )
        with self._state:
            current_z = (
                float(self.pose["z"])
                if self.pose is not None and "z" in self.pose
                else None
            )
            start_orientation = (
                (
                    float(self.pose["roll"]),
                    float(self.pose["pitch"]),
                    float(self.pose["yaw"]),
                )
                if self.pose is not None
                else None
            )
        at_current_height = current_z is not None and abs(z - current_z) <= 1e-3
        if not (MIN_POSE_Z_M <= z <= MAX_POSE_Z_M) and not at_current_height:
            raise HTTPException(
                400,
                f"z={z:.3f} m outside [{MIN_POSE_Z_M:.2f}, {MAX_POSE_Z_M:.2f}] m",
            )
        with self._motion:
            self._check_motion_locked()
            if self._stop_requested.is_set():
                raise HTTPException(409, "motion stopped by operator")
            with self._state:
                self.busy = True
                if self.joints_deg is not None:
                    self.last_good_joints = list(self.joints_deg)
            log.info(
                "linear target x=%.3f y=%.3f z=%.3f roll=%.1f pitch=%.1f yaw=%.1f",
                x,
                y,
                z,
                roll_deg,
                pitch_deg,
                yaw_deg,
            )
            try:
                if self.mock:
                    time.sleep(0.15)
                    with self._state:
                        self.pose = {
                            "x": x,
                            "y": y,
                            "z": z,
                            "roll": roll_deg,
                            "pitch": pitch_deg,
                            "yaw": yaw_deg,
                        }
                        self.updated_at = time.time()
                else:
                    target = SimpleNamespace(
                        x=float(x),
                        y=float(y),
                        z=float(z),
                        roll=math.radians(roll_deg),
                        pitch=math.radians(pitch_deg),
                        yaw=math.radians(yaw_deg),
                    )
                    with self._socket_timeout(MOTION_TIMEOUT_S):
                        self._send_linear_pose(target)
                    if self._query_collision_flag_locked():
                        raise RuntimeError(
                            "robot reported a collision during the linear move"
                        )
                    self._read_locked()
                with self._state:
                    self.last_error = None
            except HTTPException:
                raise
            except Exception as exc:
                log.exception("executor linear move failed")
                collision = self._query_collision_flag_locked()
                if collision or self._is_collision_text(str(exc)):
                    actual = self._read_after_fault_locked()
                    self._record_motion_fault_locked(exc, actual)
                    with self._state:
                        self.last_error = self.fault or f"{type(exc).__name__}: {exc}"
                    raise HTTPException(502, self.last_error) from exc
                if "goal has been aborted" in str(exc).lower() and not strict_linear:
                    log.warning(
                        "linear pose aborted; retrying with IK + joint-space path"
                    )
                    orientations = [(roll_deg, pitch_deg, yaw_deg)]
                    if start_orientation is not None:
                        orientations.append(start_orientation)
                    fallback_error: Exception | None = None
                    for fallback_roll, fallback_pitch, fallback_yaw in orientations:
                        try:
                            with self._socket_timeout(READ_TIMEOUT_S):
                                joints = self._robot.inverse_kinematics(
                                    x,
                                    y,
                                    z,
                                    math.radians(fallback_roll),
                                    math.radians(fallback_pitch),
                                    math.radians(fallback_yaw),
                                )
                            target_deg = [math.degrees(float(v)) for v in joints]
                            return self._move_locked(
                                target_deg, max_delta=MAX_POSE_DEG
                            )
                        except Exception as fallback_exc:
                            fallback_error = fallback_exc
                    log.warning(
                        "joint-space pose fallback failed: %s", fallback_error
                    )
                    with self._state:
                        self.last_error = (
                            f"target pose has no reachable IK solution: "
                            f"{fallback_error}"
                        )
                    raise HTTPException(502, self.last_error) from fallback_error
                with self._state:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    if type(exc).__name__ in CONNECTION_ERRORS:
                        self.connected = False
                raise HTTPException(502, self.last_error) from exc
            finally:
                with self._state:
                    self.busy = False
        return self.snapshot()

    def execute_pick_place(self, payload: "ExecutePickIn") -> dict[str, Any]:
        """Close, retract, move above home, orient, then place at target.

        The safe-home point is ``home + home_lift_m``. The tool is rotated to
        the placement attitude at that clear point, so the final approach is a
        translation-only path. J2 folding is rejected for this operation.
        """
        steps: list[dict[str, Any]] = []
        self._stop_requested.clear()
        try:
            self._check_motion_locked()
            with self._state:
                tool = self.tool or ""
            if "GRIPPER" not in tool:
                raise HTTPException(409, "a gripper tool is required")

            snapshot = self.snapshot()
            start_pose = snapshot.get("pose")
            if start_pose is None:
                raise HTTPException(502, "current pose is unavailable")
            start = (
                _pose_dict(payload.start.model_dump())
                if payload.start is not None
                else _pose_dict(start_pose)
            )
            target = _pose_dict(payload.target.model_dump())

            if payload.corners is not None:
                prepared, corner_meta = _prepare_cage_corners(
                    payload.corners, payload.ignored_corners
                )
                cage, fit_meta = _fit_cage_corners(
                    prepared,
                    payload.cage.opening_margin,
                    payload.cage.clearance_m,
                    payload.cage.front_top_open,
                )
                fit_meta.update(corner_meta)
                cage_source = "corners"
            else:
                cage = payload.cage
                fit_meta = None
                cage_source = "dimensions"
            # The packing target is the centre of the box front face; the
            # placement attitude is horizontal and cage-aligned.
            target = _level_placement_pose(cage, target)
            if payload.fold_j2_to_min:
                raise HTTPException(422, "J2 folding is not supported during level placement")
            if payload.home is None:
                raise HTTPException(409, "record the home pose before execution")
            # Reject an invalid placement before closing the gripper or moving.
            target_issues = _cage_target_issues(cage, target)
            if target_issues:
                return {
                    "ok": False,
                    "stage": "target_check",
                    "steps": [{"step": "target_check", "ok": False}],
                    "error": "; ".join(target_issues) + "; arm has not moved",
                }
            # Do this check before closing the gripper.  A tool point close to
            # the base can pass cage clearance checks yet lack wrist room to
            # turn to the placement yaw; the observed failure was at 0.15 m.
            preflight = plan_pick_path(
                PickPathIn(
                    start=PoseIn(**start),
                    target=PoseIn(**target),
                    approach_m=payload.approach_m,
                    gate_clearance_m=payload.gate_clearance_m,
                    payload_radius_m=payload.payload_radius_m,
                    cage=payload.cage,
                    corners=payload.corners,
                    ignored_corners=payload.ignored_corners,
                ),
                start,
                float(snapshot["max_reach_m"]),
            )
            pre_gate = next(
                (point for point in preflight["points"] if point["kind"] == "pre_gate"),
                None,
            )
            pre_gate_issues = pre_gate["issues"] if pre_gate else []
            if pre_gate_issues:
                return {
                    "ok": False,
                    "stage": "pre_gate_check",
                    "steps": [{"step": "pre_gate_check", "ok": False}],
                    "plan": preflight,
                    "error": "; ".join(pre_gate_issues) + "; arm has not moved",
                }
            frame_segments = _cage_frame_segments(cage)

            # Keep the gripper horizontal while grasping, retracting and placing.
            # Roll stays at 0°; the current yaw is kept so the operator can still
            # rotate in the horizontal plane.
            grasp_start = {
                **start,
                "roll": 0.0,
                "pitch": 0.0,
            }

            # Find a horizontal pull-back path that avoids the cage frame.
            (
                retract_points,
                retract_segments,
                retract_ok,
                retract_warnings,
            ) = _plan_retract_path(
                cage,
                frame_segments,
                grasp_start,
                payload.retract_m,
                payload.gate_clearance_m,
                payload.payload_radius_m,
            )
            warnings: list[str] = list(retract_warnings)
            start_local = _world_to_cage(
                cage, (start["x"], start["y"], start["z"])
            )
            if 0.0 < start_local[1] < cage.depth:
                warnings.append(
                    "start is inside the cage; the retract path must exit "
                    "through the opening"
                )
            retract_plan = {
                "ok": retract_ok,
                "dry_run": True,
                "cage_source": cage_source,
                "cage_fit": fit_meta,
                "points": retract_points,
                "segments": retract_segments,
                "warnings": warnings,
            }
            steps.append({"step": "retract_check", "ok": retract_plan["ok"]})
            if not retract_plan["ok"]:
                log.warning(
                    "execution retract blocked: start=%s retract_m=%.3f "
                    "warnings=%s",
                    start,
                    payload.retract_m,
                    retract_warnings,
                )
                for segment in retract_segments:
                    if segment["collision"]:
                        log.warning(
                            "blocked retract segment %s -> %s: %s",
                            segment["from"],
                            segment["to"],
                            segment["issues"],
                        )
                return {
                    "ok": False,
                    "stage": "retract",
                    "steps": steps,
                    "plan": retract_plan,
                    "error": "retract path is blocked; arm has not moved",
                }

            self.set_velocity(payload.speed_percent)
            steps.append(
                {"step": "speed", "ok": True, "percent": payload.speed_percent}
            )

            moved_to_start = False
            if payload.start is not None:
                current_start = _pose_dict(start_pose)
                distance = math.dist(
                    (current_start["x"], current_start["y"], current_start["z"]),
                    (grasp_start["x"], grasp_start["y"], grasp_start["z"]),
                )
                orientation_delta = max(
                    abs((current_start["roll"] - grasp_start["roll"] + 180.0) % 360.0 - 180.0),
                    abs((current_start["pitch"] - grasp_start["pitch"] + 180.0) % 360.0 - 180.0),
                )
                if distance > 1e-4 or orientation_delta > 0.1:
                    self.move_pose_deg(
                        grasp_start["x"],
                        grasp_start["y"],
                        grasp_start["z"],
                        grasp_start["roll"],
                        grasp_start["pitch"],
                        grasp_start["yaw"],
                    )
                    moved_to_start = True
                    steps.append({"step": "move_to_start", "ok": True, "pose": grasp_start})

            orientation_delta = max(
                abs((start["roll"] - grasp_start["roll"] + 180.0) % 360.0 - 180.0),
                abs((start["pitch"] - grasp_start["pitch"] + 180.0) % 360.0 - 180.0),
            )
            if not moved_to_start and orientation_delta > 0.1:
                self.move_pose_deg(
                    start["x"],
                    start["y"],
                    start["z"],
                    grasp_start["roll"],
                    grasp_start["pitch"],
                    grasp_start["yaw"],
                )
                steps.append({"step": "level_grasp", "ok": True, "pose": grasp_start})

            self._gripper_action(True)
            steps.append({"step": "close_gripper", "ok": True})

            for point in retract_points[1:]:
                pose = point["pose"]
                self.move_pose_deg(
                    pose["x"],
                    pose["y"],
                    pose["z"],
                    pose["roll"],
                    pose["pitch"],
                    pose["yaw"],
                )
                steps.append(
                    {
                        "step": f"move:{point['kind']}",
                        "ok": True,
                        "pose": pose,
                    }
                )

            if payload.home is None:
                raise HTTPException(
                    409,
                    "record the home pose first: Home / fold, then Home = current",
                )
            retract_last = retract_points[-1]["pose"]

            # Keep the pickup yaw at the outer retract point.  The failed
            # hardware run showed that forcing the cage yaw here put J5 at
            # 104.4 degrees (close to its +110 limit) while the tool was still
            # 4.3 degrees short.  The strict pre-gate move below aligns yaw at
            # a known clear point outside the cage instead.
            oriented_retract = {
                "x": retract_last["x"],
                "y": retract_last["y"],
                "z": retract_last["z"],
                "roll": target["roll"],
                "pitch": target["pitch"],
                "yaw": retract_last["yaw"],
            }
            self.move_pose_deg(
                oriented_retract["x"],
                oriented_retract["y"],
                oriented_retract["z"],
                oriented_retract["roll"],
                oriented_retract["pitch"],
                oriented_retract["yaw"],
            )
            steps.append({"step": "orient_retract", "ok": True, "pose": oriented_retract})
            # The retract point is still outside the cage.  Keep the carried
            # box level here, but do not require the final cage yaw yet: on the
            # Ned1 that premature wrist turn can put J5 near its upper limit.
            # The first strict pre-gate move below must reach and verify the
            # complete placement attitude before the arm crosses the opening.
            try:
                _verify_level_attitude(
                    self.snapshot().get("pose"), target, stage="outer retract"
                )
            except HTTPException as exc:
                # The outer retract pose can be kinematically unable to reach
                # an exact level while still being a safe transit point.
                # Keep it as a warning; the final target alignment is strict.
                log.warning("outer retract level warning: %s", exc.detail)

            # Do not transit through the recorded home pose here.  On this Ned
            # the home XY is close to the base and forces J5 near its upper
            # limit; turning the wrist there aborts the native linear planner.
            # The retract point is already clear of the cage, so use it as the
            # safe transit pose and let the checked path do the base rotation.
            safe_home = dict(oriented_retract)

            snapshot = self.snapshot()
            home_pose = snapshot.get("pose")
            if home_pose is None:
                raise HTTPException(502, "home pose is unavailable")
            try:
                _verify_level_attitude(home_pose, target, stage="outer retract")
            except HTTPException as exc:
                log.warning("outer retract level warning: %s", exc.detail)

            plan = plan_pick_path(
                PickPathIn(
                    start=PoseIn(**home_pose),
                    target=PoseIn(**target),
                    approach_m=payload.approach_m,
                    gate_clearance_m=payload.gate_clearance_m,
                    payload_radius_m=payload.payload_radius_m,
                    cage=payload.cage,
                    corners=payload.corners,
                    ignored_corners=payload.ignored_corners,
                ),
                home_pose,
                float(snapshot["max_reach_m"]),
            )
            plan["cage_source"] = cage_source
            plan["cage_fit"] = fit_meta
            plan["warnings"] = list(plan.get("warnings", [])) + warnings
            steps.append({"step": "plan", "ok": plan["ok"]})
            if not plan["ok"]:
                log.warning(
                    "execution plan blocked after home: home=%s target=%s warnings=%s",
                    home_pose,
                    target,
                    plan.get("warnings"),
                )
                for segment in plan.get("segments", []):
                    if segment.get("collision"):
                        log.warning(
                            "blocked segment %s -> %s: %s",
                            segment["from"],
                            segment["to"],
                            segment["issues"],
                        )
                return {
                    "ok": False,
                    "stage": "plan",
                    "steps": steps,
                    "plan": plan,
                    "error": "home-to-target path is blocked; arm stays at home",
                }

            for point in plan["points"][1:]:
                pose = point["pose"]
                self.move_pose_deg(
                    pose["x"],
                    pose["y"],
                    pose["z"],
                    pose["roll"],
                    pose["pitch"],
                    pose["yaw"],
                )
                steps.append(
                    {"step": f"move:{point['kind']}", "ok": True, "pose": pose}
                )

            # The low path can translate without actually completing the
            # wrist yaw. Do one explicit full-pose alignment at the target,
            # then verify it before release.
            self.move_pose_deg(
                target["x"],
                target["y"],
                target["z"],
                target["roll"],
                target["pitch"],
                target["yaw"],
            )
            _verify_placement_attitude(
                self.snapshot().get("pose"), target, stage="target release"
            )
            steps.append(
                {"step": "align_target", "ok": True, "pose": dict(target)}
            )
            self._gripper_action(False)
            steps.append({"step": "open_gripper", "ok": True})

            if payload.return_home_after:
                # Retrace the checked entry route, maintaining level attitude.
                for point in reversed(plan["points"][:-1]):
                    pose = {**point["pose"], **{key: target[key] for key in ("roll", "pitch", "yaw")}}
                    self.move_pose_deg(
                        pose["x"], pose["y"], pose["z"],
                        pose["roll"], pose["pitch"], pose["yaw"],
                        strict_linear=True,
                    )
                    _verify_level_attitude(
                        self.snapshot().get("pose"),
                        target,
                        stage=f"return {point['kind']}",
                    )
                steps.append(
                    {"step": "return_safe_home", "ok": True, "pose": safe_home}
                )

            return {
                "ok": True,
                "stage": "done",
                "steps": steps,
                "retract": retract_plan,
                "plan": plan,
            }
        except HTTPException as exc:
            return {"ok": False, "stage": "error", "steps": steps, "error": exc.detail}
        except Exception as exc:
            log.exception("pick/place execution failed")
            with self._state:
                error = self.fault or f"{type(exc).__name__}: {exc}"
            return {"ok": False, "stage": "error", "steps": steps, "error": error}

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
                        self.pose = {
                            "x": 0.1294,
                            "y": 0.0,
                            "z": 0.1752,
                            "roll": -0.58,
                            "pitch": 59.56,
                            "yaw": 0.0,
                        }
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
                pre_calibration = (
                    list(self.joints_deg) if self.joints_deg is not None else None
                )
                if pre_calibration is not None:
                    self.last_good_joints = list(pre_calibration)
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
                        needs_calibration = bool(self.calibration_needed)
                        learning = bool(self.learning_mode)
                    if needs_calibration or learning:
                        raise RuntimeError(
                            "auto-calibration ended with calibration still required "
                            "or motors released; clear the arm and inspect it"
                        )
                with self._state:
                    self.last_good_joints = (
                        list(self.joints_deg)
                        if self.joints_deg is not None
                        else None
                    )
                    self.last_error = None
            except HTTPException:
                raise
            except Exception as exc:
                actual = self._read_after_fault_locked()
                if pre_calibration is not None:
                    with self._state:
                        self.last_good_joints = list(pre_calibration)
                self._record_motion_fault_locked(exc, actual)
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
        missing = [p for p in poses if p not in pose_names and p not in SEQUENCE_ACTION_STEPS]
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
        pad = max(8.0, min(rw, rh) * 0.15)
        boxes = [
            (
                max(0, int(math.floor(rx - pad))),
                max(0, int(math.floor(ry - pad))),
                min(w, int(math.ceil(rx + rw + pad))),
                min(h, int(math.ceil(ry + rh + pad))),
            )
        ]
    else:
        boxes = []
        radii = (radius, min(220, radius * 2), min(max(w, h), radius * 5))
        for r in dict.fromkeys(radii):
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
        min_dim = min(gray.shape[:2])
        scales = (1, 2, 4) if min_dim >= 100 else (2, 4, 6)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        attempts: list[tuple[np.ndarray, int]] = []
        for scale in scales:
            resized = (
                gray
                if scale == 1
                else cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            )
            attempts.append((resized, scale))
        enhanced = clahe.apply(gray)
        fallback_scale = 4 if min_dim >= 50 else 6
        enhanced = cv2.resize(
            enhanced,
            None,
            fx=fallback_scale,
            fy=fallback_scale,
            interpolation=cv2.INTER_CUBIC,
        )
        attempts.extend(
            [
                (enhanced, fallback_scale),
                (
                    cv2.threshold(
                        enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
                    )[1],
                    fallback_scale,
                ),
                (
                    cv2.adaptiveThreshold(
                        enhanced,
                        255,
                        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                        cv2.THRESH_BINARY,
                        31,
                        5,
                    ),
                    fallback_scale,
                ),
            ]
        )
        selection_center = np.asarray(
            [x if roi is None else rx + rw / 2, y if roi is None else ry + rh / 2],
            dtype=np.float32,
        )
        detector = cv2.QRCodeDetector()
        for variant, scale in attempts:
            candidates: list[np.ndarray] = []
            if hasattr(detector, "detectMulti"):
                found, multi_points = detector.detectMulti(variant)
                if found and multi_points is not None:
                    candidates.extend(np.asarray(multi_points, dtype=np.float32).reshape(-1, 4, 2))
            if not candidates:
                found, single_points = detector.detect(variant)
                if found and single_points is not None:
                    candidates.append(np.asarray(single_points, dtype=np.float32).reshape(4, 2))
            stage_best: dict[str, Any] | None = None
            stage_score = math.inf
            for points in candidates:
                full = points / scale + np.array([x0, y0], dtype=np.float32)
                full = _refine_qr_corners(image, full)
                sides = np.linalg.norm(full - np.roll(full, 1, axis=0), axis=1)
                if float(sides.min()) < 4.0 or float(sides.max() / sides.min()) > 8.0:
                    continue
                contour = full.astype(np.float32).reshape(-1, 1, 2)
                if not cv2.isContourConvex(contour):
                    continue
                area = abs(float(cv2.contourArea(contour)))
                if area < 36.0:
                    continue
                center = _qr_diagonal_center(full)
                edge = full[1] - full[0]
                angle = (
                    math.degrees(math.atan2(float(edge[1]), float(edge[0]))) + 180
                ) % 360 - 180
                score = float(np.linalg.norm(center - selection_center)) / max(
                    1.0, math.hypot(x1 - x0, y1 - y0)
                )
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
                if score < stage_score:
                    stage_best, stage_score = candidate, score
            if stage_best is not None:
                best = stage_best
                break
        if best is not None:
            break
    return best or {"ok": False, "frame": {"w": w, "h": h}, "error": "QR code not found near click"}


def _refine_qr_corners(image: Any, points: np.ndarray) -> np.ndarray:
    """Refine detector corners at original resolution when a local window fits."""
    import cv2

    h, w = image.shape[:2]
    if np.any(points[:, 0] < 4) or np.any(points[:, 0] >= w - 4):
        return points
    if np.any(points[:, 1] < 4) or np.any(points[:, 1] >= h - 4):
        return points
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    refined = points.astype(np.float32).reshape(-1, 1, 2)
    cv2.cornerSubPix(
        gray,
        refined,
        (3, 3),
        (-1, -1),
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.01),
    )
    return refined.reshape(4, 2)


def _qr_diagonal_center(points: np.ndarray) -> np.ndarray:
    """Projectively correct centre: intersection of the two diagonals."""
    p0, p1, p2, p3 = np.asarray(points, dtype=np.float64)
    a = np.column_stack((p2 - p0, -(p3 - p1)))
    try:
        t = float(np.linalg.solve(a, p1 - p0)[0])
    except np.linalg.LinAlgError:
        return np.asarray(points, dtype=np.float32).mean(axis=0)
    return np.asarray(p0 + t * (p2 - p0), dtype=np.float32)


_camera_info_cache: tuple[float, dict[str, Any]] | None = None


def _camera_intrinsics(frame_w: int, frame_h: int) -> tuple[np.ndarray, np.ndarray]:
    """Load D455 RGB intrinsics and scale them to the submitted frame size."""
    global _camera_info_cache

    data: dict[str, Any] | None = None
    if CAMERA_INTRINSICS_FILE.exists():
        try:
            data = json.loads(CAMERA_INTRINSICS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
    if data is None and _camera_info_cache and time.time() - _camera_info_cache[0] < 60:
        data = _camera_info_cache[1]
    if data is None:
        try:
            with urllib.request.urlopen(CAMERA_INFO_URL, timeout=3) as response:
                data = json.loads(response.read())
            _camera_info_cache = (time.time(), data)
        except Exception as exc:
            raise HTTPException(503, f"RGB camera intrinsics unavailable: {exc}") from exc
    k = np.asarray(data.get("k"), dtype=np.float64)
    d = np.asarray(data.get("d", []), dtype=np.float64)
    source_w = int(data.get("width", 0))
    source_h = int(data.get("height", 0))
    if k.size != 9 or source_w <= 0 or source_h <= 0:
        raise HTTPException(503, "RGB camera intrinsics are incomplete")
    matrix = k.reshape(3, 3).copy()
    sx, sy = frame_w / source_w, frame_h / source_h
    matrix[0, 0] *= sx
    matrix[0, 2] *= sx
    matrix[1, 1] *= sy
    matrix[1, 2] *= sy
    return matrix, d.reshape(-1, 1)


def _estimate_qr_camera_pose(
    corners: list[dict[str, float]], frame_w: int, frame_h: int
) -> dict[str, Any]:
    import cv2

    half = QR_CODE_SIZE_M / 2
    object_points = np.asarray(
        [
            [-half, half, 0.0],
            [half, half, 0.0],
            [half, -half, 0.0],
            [-half, -half, 0.0],
        ],
        dtype=np.float64,
    )
    image_points = np.asarray([[p["x"], p["y"]] for p in corners], dtype=np.float64)
    camera_matrix, distortion = _camera_intrinsics(frame_w, frame_h)
    ok, rvec, tvec = cv2.solvePnP(
        object_points,
        image_points,
        camera_matrix,
        distortion,
        flags=cv2.SOLVEPNP_IPPE_SQUARE,
    )
    if not ok or float(tvec.reshape(3)[2]) <= 0:
        ok, rvec, tvec = cv2.solvePnP(
            object_points,
            image_points,
            camera_matrix,
            distortion,
            flags=cv2.SOLVEPNP_ITERATIVE,
        )
    if not ok or float(tvec.reshape(3)[2]) <= 0:
        raise HTTPException(422, "QR pose could not be estimated from its corners")
    projected, _ = cv2.projectPoints(
        object_points, rvec, tvec, camera_matrix, distortion
    )
    reprojection = float(
        np.sqrt(np.mean(np.sum((projected.reshape(-1, 2) - image_points) ** 2, axis=1)))
    )
    return {
        "rvec": [round(float(v), 9) for v in rvec.reshape(3)],
        "tvec": [round(float(v), 9) for v in tvec.reshape(3)],
        "reprojection_error_px": round(reprojection, 3),
        "qr_size_m": QR_CODE_SIZE_M,
    }


def _estimate_charuco_camera_pose(image: Any) -> dict[str, Any]:
    """Estimate a fixed 5x7 ChArUco board pose for hand-eye calibration."""
    import cv2

    h, w = image.shape[:2]
    camera_matrix, distortion = _camera_intrinsics(w, h)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    board = cv2.aruco.CharucoBoard(
        (CHARUCO_SQUARES_X, CHARUCO_SQUARES_Y),
        CHARUCO_SQUARE_M,
        CHARUCO_MARKER_M,
        dictionary,
    )
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if hasattr(cv2.aruco, "CharucoDetector"):
        detector = cv2.aruco.CharucoDetector(board)
        charuco_corners, charuco_ids, _, _ = detector.detectBoard(gray)
    else:  # OpenCV 4.9 compatibility on the robot
        marker_corners, marker_ids, _ = cv2.aruco.detectMarkers(gray, dictionary)
        if marker_ids is None:
            charuco_corners, charuco_ids = None, None
        else:
            _, charuco_corners, charuco_ids = cv2.aruco.interpolateCornersCharuco(
                marker_corners,
                marker_ids,
                gray,
                board,
                cameraMatrix=camera_matrix,
                distCoeffs=distortion,
            )
    count = 0 if charuco_ids is None else len(charuco_ids)
    if count < 8:
        raise HTTPException(422, f"ChArUco board needs at least 8 visible corners; found {count}")
    if hasattr(board, "matchImagePoints"):
        object_points, image_points = board.matchImagePoints(charuco_corners, charuco_ids)
        ok, rvec, tvec = cv2.solvePnP(
            object_points,
            image_points,
            camera_matrix,
            distortion,
            flags=cv2.SOLVEPNP_ITERATIVE,
        )
    else:
        ok, rvec, tvec = cv2.aruco.estimatePoseCharucoBoard(
            charuco_corners,
            charuco_ids,
            board,
            camera_matrix,
            distortion,
            None,
            None,
        )
        object_points, image_points = board.matchImagePoints(charuco_corners, charuco_ids)
    if not ok:
        raise HTTPException(422, "ChArUco board pose could not be estimated")
    projected, _ = cv2.projectPoints(
        object_points, rvec, tvec, camera_matrix, distortion
    )
    reprojection = float(
        np.sqrt(
            np.mean(
                np.sum(
                    (projected.reshape(-1, 2) - np.asarray(image_points).reshape(-1, 2)) ** 2,
                    axis=1,
                )
            )
        )
    )
    return {
        "rvec": [round(float(v), 9) for v in rvec.reshape(3)],
        "tvec": [round(float(v), 9) for v in tvec.reshape(3)],
        "reprojection_error_px": round(reprojection, 3),
        "corners": count,
    }


def _rpy_rotation(roll: float, pitch: float, yaw: float) -> np.ndarray:
    rx, ry, rz = map(math.radians, (roll, pitch, yaw))
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    return np.asarray(
        [
            [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
            [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
            [-sy, cy * sx, cy * cx],
        ],
        dtype=np.float64,
    )


def _pose_transform(pose: dict[str, float]) -> np.ndarray:
    transform = np.eye(4, dtype=np.float64)
    transform[:3, :3] = _rpy_rotation(pose["roll"], pose["pitch"], pose["yaw"])
    transform[:3, 3] = [pose["x"], pose["y"], pose["z"]]
    return transform


def _transform_pose(transform: np.ndarray) -> dict[str, float]:
    rotation = transform[:3, :3]
    pitch = math.atan2(-rotation[2, 0], math.hypot(rotation[0, 0], rotation[1, 0]))
    if abs(math.cos(pitch)) > 1e-8:
        roll = math.atan2(rotation[2, 1], rotation[2, 2])
        yaw = math.atan2(rotation[1, 0], rotation[0, 0])
    else:
        roll = 0.0
        yaw = math.atan2(-rotation[0, 1], rotation[1, 1])
    return {
        "x": round(float(transform[0, 3]), 6),
        "y": round(float(transform[1, 3]), 6),
        "z": round(float(transform[2, 3]), 6),
        "roll": round(math.degrees(roll), 4),
        "pitch": round(math.degrees(pitch), 4),
        "yaw": round(math.degrees(yaw), 4),
    }


def _load_hand_eye_calibration() -> dict[str, Any]:
    empty = {"samples": [], "camera_to_tool": None, "needed": 10, "qr_size_m": QR_CODE_SIZE_M}
    if not HAND_EYE_CALIBRATION_FILE.exists():
        return empty
    try:
        data = json.loads(HAND_EYE_CALIBRATION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    data.setdefault("samples", [])
    data.setdefault("camera_to_tool", None)
    data["needed"] = 10
    data["qr_size_m"] = QR_CODE_SIZE_M
    return data


def _save_hand_eye_calibration(data: dict[str, Any]) -> None:
    HAND_EYE_CALIBRATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = HAND_EYE_CALIBRATION_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(HAND_EYE_CALIBRATION_FILE)


def _solve_hand_eye(samples: list[dict[str, Any]]) -> dict[str, Any]:
    import cv2

    if len(samples) < 10:
        raise HTTPException(409, "at least ten stationary wrist-camera samples are required")
    gripper_rotations: list[np.ndarray] = []
    gripper_translations: list[np.ndarray] = []
    target_rotations: list[np.ndarray] = []
    target_translations: list[np.ndarray] = []
    for sample in samples:
        tool = _pose_transform(sample["arm_pose"])
        gripper_rotations.append(tool[:3, :3])
        gripper_translations.append(tool[:3, 3].reshape(3, 1))
        target_rotation, _ = cv2.Rodrigues(
            np.asarray(sample["camera_rvec"], dtype=np.float64).reshape(3, 1)
        )
        target_rotations.append(target_rotation)
        target_translations.append(
            np.asarray(sample["camera_tvec"], dtype=np.float64).reshape(3, 1)
        )
    camera_to_tool_rotation, camera_to_tool_translation = cv2.calibrateHandEye(
        gripper_rotations,
        gripper_translations,
        target_rotations,
        target_translations,
        method=cv2.CALIB_HAND_EYE_PARK,
    )
    if not np.all(np.isfinite(camera_to_tool_rotation)) or not np.all(
        np.isfinite(camera_to_tool_translation)
    ):
        raise HTTPException(422, "hand-eye calibration did not produce a finite transform")
    return {
        "rotation": camera_to_tool_rotation.tolist(),
        "translation": camera_to_tool_translation.reshape(3).tolist(),
    }


def _qr_base_pose(
    arm_pose: dict[str, float],
    camera_pose: dict[str, Any],
    camera_to_tool: dict[str, Any],
    target_offset: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> dict[str, float]:
    import cv2

    tool_from_camera = np.eye(4, dtype=np.float64)
    tool_from_camera[:3, :3] = np.asarray(camera_to_tool["rotation"], dtype=np.float64)
    tool_from_camera[:3, 3] = np.asarray(camera_to_tool["translation"], dtype=np.float64)
    camera_from_qr = np.eye(4, dtype=np.float64)
    camera_from_qr[:3, :3], _ = cv2.Rodrigues(
        np.asarray(camera_pose["rvec"], dtype=np.float64).reshape(3, 1)
    )
    camera_from_qr[:3, 3] = np.asarray(camera_pose["tvec"], dtype=np.float64)
    qr_from_target = np.eye(4, dtype=np.float64)
    qr_from_target[:3, 3] = target_offset
    return _transform_pose(
        _pose_transform(arm_pose) @ tool_from_camera @ camera_from_qr @ qr_from_target
    )


def _decode_submitted_image(data_url: str) -> Any:
    if len(data_url) > 4_000_000 or not data_url.startswith("data:image/"):
        raise HTTPException(413, "submitted camera frame is invalid or too large")
    try:
        _, encoded = data_url.split(",", 1)
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "submitted camera frame is not valid base64") from exc
    import cv2

    image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(400, "submitted camera frame is not a valid image")
    return image


def _require_stationary_arm(controller: ArmController, captured_pose: dict[str, float]) -> None:
    if not all(math.isfinite(float(value)) for value in captured_pose.values()):
        raise HTTPException(422, "captured arm pose contains non-finite values")
    after_status = controller.status()
    after_pose = after_status.get("pose")
    position_delta = (
        math.dist(
            [captured_pose[key] for key in ("x", "y", "z")],
            [after_pose[key] for key in ("x", "y", "z")],
        )
        if after_pose is not None
        else math.inf
    )
    angle_delta = (
        max(
            abs((captured_pose[key] - after_pose[key] + 180) % 360 - 180)
            for key in ("roll", "pitch", "yaw")
        )
        if after_pose is not None
        else math.inf
    )
    if (
        not after_status.get("connected")
        or after_status.get("busy")
        or position_delta > 0.002
        or angle_delta > 1.0
    ):
        raise HTTPException(
            409,
            "arm moved while the frozen frame was processed; stop the arm and capture again",
        )


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


# ── Dry-run pick path planning (cage frame avoidance) ────────────────────────
PATH_SAMPLE_M = 0.005  # 5 mm sampling along each path segment
MIN_RETRACT_RADIUS_M = 0.18  # do not pull back into the base/singular zone
# A pre-gate point is an approach point just outside the opening, not a
# retract into the base. The opening can be closer than the retract exclusion
# on this fixed cage; use a slightly smaller allowance for that one point.
MIN_PRE_GATE_RADIUS_M = 0.16
def _path_cage_for_payload(cage: CageIn, payload_radius_m: float) -> CageIn:
    """Inflate cage clearance by the carried payload's swept radius."""
    if payload_radius_m <= 1e-9:
        return cage
    return cage.model_copy(update={"clearance_m": cage.clearance_m + payload_radius_m})


def _cage_to_world(cage: CageIn, local: tuple[float, float, float]) -> tuple[float, float, float]:
    """Cage-local (X width, Y depth, Z height) -> arm base frame."""
    yaw = math.radians(cage.yaw)
    x, y, z = local
    return (
        cage.x + x * math.cos(yaw) - y * math.sin(yaw),
        cage.y + x * math.sin(yaw) + y * math.cos(yaw),
        cage.z + z,
    )


def _world_to_cage(cage: CageIn, point: tuple[float, float, float]) -> tuple[float, float, float]:
    """Arm base frame -> cage-local coordinates."""
    yaw = math.radians(cage.yaw)
    dx = point[0] - cage.x
    dy = point[1] - cage.y
    return (
        dx * math.cos(yaw) + dy * math.sin(yaw),
        -dx * math.sin(yaw) + dy * math.cos(yaw),
        point[2] - cage.z,
    )


def _prepare_cage_corners(
    points: list[Vec3In],
    ignored: list[int],
) -> tuple[list[Vec3In], dict[str, Any]]:
    """Normalise the taught corners before fitting.

    - BR/BL bottom are swapped automatically when their width edge runs the
      opposite way to the front edge (a common teaching-order mistake).
    - The unreachable back-top corners (6, 7) are derived from the back-bottom
      corners plus the average front height vector.
    """
    if len(points) != 8:
        raise HTTPException(422, "exactly 8 cage corners are required")
    ignored_set = {int(index) for index in ignored}
    if not ignored_set.issubset({6, 7}):
        raise HTTPException(
            422, "only the back-top corners (indices 6 and 7) can be ignored"
        )
    p = [(float(v.x), float(v.y), float(v.z)) for v in points]

    def add(a, b):
        return (a[0] + b[0], a[1] + b[1], a[2] + b[2])

    def sub(a, b):
        return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

    def dot(a, b):
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

    reordered: list[int] = []
    width_front = sub(p[1], p[0])
    width_back = sub(p[2], p[3])
    if dot(width_front, width_back) < 0.0:
        # The two back-bottom corners were taught in the opposite order.
        p[2], p[3] = p[3], p[2]
        p[6], p[7] = p[7], p[6]
        reordered = [2, 3]

    height = (
        (p[4][0] - p[0][0] + p[5][0] - p[1][0]) * 0.5,
        (p[4][1] - p[0][1] + p[5][1] - p[1][1]) * 0.5,
        (p[4][2] - p[0][2] + p[5][2] - p[1][2]) * 0.5,
    )
    if 6 in ignored_set:
        p[6] = add(p[2], height)
    if 7 in ignored_set:
        p[7] = add(p[3], height)

    out = [Vec3In(x=q[0], y=q[1], z=q[2]) for q in p]
    return out, {
        "ignored_corners": sorted(ignored_set),
        "reconstructed_corners": sorted(ignored_set),
        "reordered_corners": reordered,
    }


def _fit_cage_corners(
    points: list[Vec3In],
    margin: float,
    clearance: float,
    front_top_open: bool,
) -> tuple[CageIn, dict[str, Any]]:
    """Fit an ideal box to eight taught corners, in the arm base frame.

    Corner order (viewed from the arm, opening first):
      0 front-left-bottom, 1 front-right-bottom,
      2 back-right-bottom, 3 back-left-bottom,
      4 front-left-top,    5 front-right-top,
      6 back-right-top,    7 back-left-top.
    """
    if len(points) != 8:
        raise HTTPException(422, "exactly 8 cage corners are required")
    p = [(float(v.x), float(v.y), float(v.z)) for v in points]

    def sub(a, b):
        return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

    def add(a, b):
        return (a[0] + b[0], a[1] + b[1], a[2] + b[2])

    def mul(v, s):
        return (v[0] * s, v[1] * s, v[2] * s)

    def dot(a, b):
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

    def cross(a, b):
        return (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )

    def length(v):
        return math.sqrt(dot(v, v))

    def unit(v):
        n = length(v)
        if n < 1e-9:
            raise HTTPException(422, "cage corners are degenerate")
        return (v[0] / n, v[1] / n, v[2] / n)

    p0, p1, p2, p3, p4, p5, _, _ = p

    # Fit the floor rectangle from the four bottom corners, then use the two
    # front vertical edges only for the height. The top corners are often
    # unreachable and would otherwise skew the width/depth.
    front_center = mul(add(p0, p1), 0.5)
    back_center = mul(add(p2, p3), 0.5)
    center_bottom = mul(add(add(p0, p1), add(p2, p3)), 0.25)

    width_front = sub(p1, p0)
    width_back = sub(p2, p3)
    width = unit(add(width_front, width_back))
    depth_raw = sub(back_center, front_center)
    depth = unit(depth_raw)
    depth = unit(sub(depth, mul(width, dot(depth, width))))
    height = unit(cross(width, depth))
    if height[2] < 0.0:
        height = mul(height, -1.0)

    w = (length(width_front) + length(width_back)) * 0.5
    d = length(depth_raw)
    h = (
        abs(dot(sub(p4, p0), height)) + abs(dot(sub(p5, p1), height))
    ) * 0.5
    if h < 0.02:
        h = abs(max(q[2] for q in p) - min(q[2] for q in p))

    origin = sub(center_bottom, mul(depth, d * 0.5))
    yaw = math.degrees(math.atan2(-depth[0], depth[1]))
    center = add(origin, add(mul(depth, d * 0.5), mul(height, h * 0.5)))

    cage = CageIn(
        x=origin[0],
        y=origin[1],
        z=origin[2],
        yaw=yaw,
        width=w,
        depth=d,
        height=h,
        opening_margin=margin,
        clearance_m=clearance,
        front_top_open=front_top_open,
    )

    width_edges = ((0, 1), (2, 3))
    depth_edges = ((0, 3), (1, 2))
    height_edges = ((0, 4), (1, 5))

    def edge_spread(edges) -> float:
        lengths = [length(sub(p[b], p[a])) for a, b in edges]
        return max(lengths) - min(lengths)

    spreads = {
        "width_m": edge_spread(width_edges),
        "depth_m": edge_spread(depth_edges),
        "height_m": edge_spread(height_edges),
    }
    orthogonality = abs(dot(unit(width_front), unit(depth_raw)))
    warnings: list[str] = []
    for name, spread in spreads.items():
        if spread > 0.03:
            warnings.append(f"{name.replace('_m', '')} edges differ by {spread * 1000:.0f} mm")
    if orthogonality > 0.15:
        warnings.append(f"floor axes are not orthogonal (dot {orthogonality:.2f})")

    fit = {
        "width_m": round(w, 4),
        "depth_m": round(d, 4),
        "height_m": round(h, 4),
        "yaw_deg": round(yaw, 2),
        "origin": {"x": origin[0], "y": origin[1], "z": origin[2]},
        "center": {"x": center[0], "y": center[1], "z": center[2]},
        "edge_spread_m": {name: round(value, 4) for name, value in spreads.items()},
        "orthogonality": round(orthogonality, 4),
        "fit_method": "bottom_corners_plus_height",
        "warnings": warnings,
    }
    return cage, fit


def _corner_edge_segments(
    points: list[Vec3In],
    front_top_open: bool,
) -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
    """The 11 physical cage edges (front-top edge dropped when it is open)."""
    if len(points) != 8:
        raise HTTPException(422, "exactly 8 cage corners are required")
    p = [(float(v.x), float(v.y), float(v.z)) for v in points]
    edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]
    if front_top_open:
        edges = [edge for edge in edges if edge != (4, 5)]
    return [(p[a], p[b]) for a, b in edges]


def _cage_frame_segments(
    cage: CageIn,
) -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
    """Outer edges plus the opening frame; front-top edge is optional."""
    w, d, h = cage.width, cage.depth, cage.height
    half = w / 2.0
    corners = [
        (-half, 0.0, 0.0),
        (half, 0.0, 0.0),
        (half, d, 0.0),
        (-half, d, 0.0),
        (-half, 0.0, h),
        (half, 0.0, h),
        (half, d, h),
        (-half, d, h),
    ]
    edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]
    if cage.front_top_open:
        edges = [edge for edge in edges if edge != (4, 5)]
    segments = [
        (_cage_to_world(cage, corners[a]), _cage_to_world(cage, corners[b]))
        for a, b in edges
    ]
    margin = cage.opening_margin
    x0, x1 = -half + margin, half - margin
    z0 = margin
    z1 = h if cage.front_top_open else h - margin
    if x1 > x0 and z1 > z0:
        opening = [
            (x0, 0.0, z0),
            (x1, 0.0, z0),
            (x1, 0.0, z1),
            (x0, 0.0, z1),
        ]
        opening_edges = [(0, 1), (1, 2), (2, 3), (3, 0)]
        if cage.front_top_open:
            opening_edges = [(0, 1), (1, 2), (3, 0)]
        segments += [
            (_cage_to_world(cage, opening[a]), _cage_to_world(cage, opening[b]))
            for a, b in opening_edges
        ]
    return segments


def _point_segment_distance(
    point: tuple[float, float, float],
    a: tuple[float, float, float],
    b: tuple[float, float, float],
) -> float:
    ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    ap = (point[0] - a[0], point[1] - a[1], point[2] - a[2])
    denom = ab[0] ** 2 + ab[1] ** 2 + ab[2] ** 2
    if denom <= 1e-12:
        return math.dist(point, a)
    t = (ab[0] * ap[0] + ab[1] * ap[1] + ab[2] * ap[2]) / denom
    t = max(0.0, min(1.0, t))
    closest = (a[0] + ab[0] * t, a[1] + ab[1] * t, a[2] + ab[2] * t)
    return math.dist(point, closest)


def _lerp3(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
    t: float,
) -> tuple[float, float, float]:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def _shell_crossing_issues(
    p0: tuple[float, float, float],
    p1: tuple[float, float, float],
    cage: CageIn,
) -> list[str]:
    """Treat the cage as a closed box with one opening on the Y=0 face."""
    issues: list[str] = []
    w, d, h, margin = cage.width, cage.depth, cage.height, cage.opening_margin
    half = w / 2.0

    def crosses(a: float, b: float, plane: float) -> bool:
        return (a - plane) * (b - plane) < 0.0

    def crossing(index: int, plane: float) -> tuple[float, float, float]:
        t = (plane - p0[index]) / (p1[index] - p0[index])
        return _lerp3(p0, p1, t)

    if crosses(p0[1], p1[1], 0.0):
        x, _, z = crossing(1, 0.0)
        z_hi = h if cage.front_top_open else h - margin
        if not (-half + margin <= x <= half - margin and margin <= z <= z_hi):
            issues.append("crosses the front frame outside the opening")
    if crosses(p0[1], p1[1], d):
        x, _, z = crossing(1, d)
        if -half <= x <= half and 0.0 <= z <= h:
            issues.append("crosses the back frame")
    if crosses(p0[0], p1[0], -half):
        _, y, z = crossing(0, -half)
        if 0.0 <= y <= d and 0.0 <= z <= h:
            issues.append("crosses the left frame")
    if crosses(p0[0], p1[0], half):
        _, y, z = crossing(0, half)
        if 0.0 <= y <= d and 0.0 <= z <= h:
            issues.append("crosses the right frame")
    if crosses(p0[2], p1[2], h):
        x, y, _ = crossing(2, h)
        if -half <= x <= half and 0.0 <= y <= d:
            issues.append("crosses the top frame")
    if crosses(p0[2], p1[2], 0.0):
        x, y, _ = crossing(2, 0.0)
        if -half <= x <= half and 0.0 <= y <= d:
            issues.append("crosses the floor frame")
    return issues


def _segment_cage_check(
    cage: CageIn,
    frame_segments: list[tuple[tuple[float, float, float], tuple[float, float, float]]],
    a: tuple[float, float, float],
    b: tuple[float, float, float],
) -> tuple[float, list[str]]:
    """Sample one path segment and check clearance to the cage frame/walls."""
    length = math.dist(a, b)
    steps = max(1, int(math.ceil(length / PATH_SAMPLE_M)))
    min_clearance = math.inf
    issues: list[str] = []
    half_w = cage.width / 2.0
    previous_local = _world_to_cage(cage, a)
    for i in range(steps + 1):
        t = i / steps
        point = _lerp3(a, b, t)
        local = _world_to_cage(cage, point)
        distance = min(
            _point_segment_distance(point, seg[0], seg[1]) for seg in frame_segments
        )
        wall_gap = math.inf
        if (
            -half_w <= local[0] <= half_w
            and 0.0 < local[1] <= cage.depth
            and 0.0 <= local[2] <= cage.height
        ):
            # Conservative clearance to the closed side/back/top/floor faces.
            wall_gap = min(
                local[0] + half_w,
                half_w - local[0],
                cage.depth - local[1],
                local[2],
                cage.height - local[2],
            )
            if wall_gap + 1e-9 < cage.clearance_m:
                issues.append(
                    f"wall clearance {wall_gap * 1000:.0f} mm "
                    f"< {cage.clearance_m * 1000:.0f} mm"
                )
        min_clearance = min(min_clearance, distance, wall_gap)
        if distance + 1e-9 < cage.clearance_m:
            issues.append(
                f"frame clearance {distance * 1000:.0f} mm "
                f"< {cage.clearance_m * 1000:.0f} mm"
            )
        if i:
            issues.extend(_shell_crossing_issues(previous_local, local, cage))
        previous_local = local
    return min_clearance, sorted(set(issues))


def _plan_retract_path(
    cage: CageIn,
    frame_segments: list[tuple[tuple[float, float, float], tuple[float, float, float]]],
    start: dict[str, float],
    retract_m: float,
    gate_clearance: float,
    payload_radius_m: float = 0.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool, list[str]]:
    """Find a horizontal pull-back path that avoids the cage frame.

    Preference: direct toward the base, then an outward/tangent detour. Every
    leg uses the same 5 mm clearance check as the dry-run planner.
    """
    x, y, z = start["x"], start["y"], start["z"]
    radius = math.hypot(x, y)
    target_radius = max(MIN_RETRACT_RADIUS_M, radius - retract_m)
    if radius <= target_radius + 1e-6:
        return (
            [
                {
                    "kind": "start",
                    "pose": dict(start),
                    "zone": "outside",
                    "reach_m": round(radius, 4),
                    "issues": [],
                }
            ],
            [],
            True,
            [
                f"start radius {radius:.3f} m is already at the "
                f"{target_radius:.3f} m pull-back radius"
            ],
        )

    def at_limit(px: float, py: float) -> tuple[float, float]:
        r = math.hypot(px, py)
        if r <= target_radius + 1e-6:
            return px, py
        scale = target_radius / r
        return px * scale, py * scale

    def pose_at(px: float, py: float) -> dict[str, float]:
        return {**start, "x": px, "y": py, "z": z}

    ux, uy = -x / radius, -y / radius
    direct = at_limit(x, y)
    yaw = math.radians(cage.yaw)
    nx, ny = math.sin(yaw), -math.cos(yaw)
    cage_center = _cage_to_world(cage, (0.0, cage.depth / 2.0, 0.0))
    ax, ay = x - cage_center[0], y - cage_center[1]
    away_n = math.hypot(ax, ay)
    if away_n > 1e-6:
        ax, ay = ax / away_n, ay / away_n
    else:
        ax, ay = nx, ny

    candidates: list[list[tuple[float, float]]] = [[(x, y), direct]]
    directions: list[tuple[float, float]] = []
    for degrees in range(0, 360, 22):
        radians = math.radians(degrees)
        directions.append((math.cos(radians), math.sin(radians)))
    directions.extend([(ax, ay), (nx, ny), (-nx, -ny), (ux, uy)])

    unique: list[tuple[float, float]] = []
    for dx, dy in directions:
        n = math.hypot(dx, dy)
        if n <= 1e-6:
            continue
        dx, dy = dx / n, dy / n
        if not any(
            abs(dx - ex) < 1e-3 and abs(dy - ey) < 1e-3 for ex, ey in unique
        ):
            unique.append((dx, dy))
    # Prefer moving away from the cage, then toward the base.
    unique.sort(
        key=lambda d: -(d[0] * ax + d[1] * ay) - 0.5 * (d[0] * ux + d[1] * uy)
    )
    for distance in (0.05, 0.10, 0.15):
        for dx, dy in unique:
            mid = (x + dx * distance, y + dy * distance)
            candidates.append([(x, y), mid, at_limit(*mid)])

    def build(coords: list[tuple[float, float]]):
        deduped = [coords[0]]
        for point in coords[1:]:
            if math.dist(point, deduped[-1]) > 1e-4:
                deduped.append(point)
        deduped[-1] = at_limit(*deduped[-1])
        names = ["start"] + [
            f"retract{i}" for i in range(1, len(deduped) - 1)
        ] + ["retract"]
        points = [
            {
                "kind": name,
                "pose": pose_at(px, py),
                "zone": "outside",
                "reach_m": round(math.hypot(px, py), 4),
                "issues": [],
            }
            for name, (px, py) in zip(names, deduped)
        ]
        segments = []
        ok = True
        for a, b in zip(points, points[1:]):
            pa = (a["pose"]["x"], a["pose"]["y"], a["pose"]["z"])
            pb = (b["pose"]["x"], b["pose"]["y"], b["pose"]["z"])
            min_clearance, issues = _segment_cage_check(
                _path_cage_for_payload(cage, payload_radius_m),
                frame_segments,
                pa,
                pb,
            )
            if issues:
                ok = False
            segments.append(
                {
                    "from": a["kind"],
                    "to": b["kind"],
                    "length_m": round(math.dist(pa, pb), 4),
                    "min_clearance_m": (
                        round(min_clearance, 4)
                        if math.isfinite(min_clearance)
                        else None
                    ),
                    "collision": bool(issues),
                    "issues": issues,
                }
            )
        return points, segments, ok

    direct_points, direct_segments, direct_ok = build(candidates[0])
    if direct_ok:
        return direct_points, direct_segments, True, []
    for coords in candidates[1:]:
        points, segments, ok = build(coords)
        if ok:
            return (
                points,
                segments,
                True,
                ["direct retract was blocked; routed around the frame"],
            )
    return direct_points, direct_segments, False, ["no clear retract path found"]


def _pose_dict(pose: dict[str, Any]) -> dict[str, float]:
    return {key: float(pose[key]) for key in ("x", "y", "z", "roll", "pitch", "yaw")}


def _cage_target_issues(cage: CageIn, target: dict[str, float]) -> list[str]:
    """Placement targets must be inside the taught cage with wall clearance."""
    x, y, z = _world_to_cage(cage, (target["x"], target["y"], target["z"]))
    if not all(math.isfinite(value) for value in (x, y, z)):
        return ["target coordinates must be finite"]
    if not (-cage.width / 2 <= x <= cage.width / 2 and 0 <= y <= cage.depth and 0 <= z <= cage.height):
        return [
            "target is outside the taught cage "
            f"(local x/y/z {x * 1000:.0f}/{y * 1000:.0f}/{z * 1000:.0f} mm); "
            "check packing dimensions, opening-centre origin and cage teaching"
        ]
    gap = min(x + cage.width / 2, cage.width / 2 - x, cage.depth - y, z, cage.height - z)
    if gap + 1e-9 < cage.clearance_m:
        return [f"target wall clearance {gap * 1000:.0f} mm < {cage.clearance_m * 1000:.0f} mm"]
    return []


def _level_placement_pose(
    cage: CageIn, pose: dict[str, float], preserve_yaw: bool = False
) -> dict[str, float]:
    # Table-level side-entry convention: roll=0, pitch=0. Tool forward points
    # along cage +Y, hence cage yaw + 90 degrees. ``preserve_yaw`` keeps the
    # operator's grasp yaw when the cage yaw is not reachable on this Ned.
    yaw = pose["yaw"] if preserve_yaw else (cage.yaw + 270.0) % 360.0 - 180.0
    return {**pose, "roll": 0.0, "pitch": 0.0, "yaw": yaw}


def _attitude_errors(
    actual: dict[str, float] | None, target: dict[str, float]
) -> dict[str, float] | None:
    if actual is None:
        return None
    errors: dict[str, float] = {}
    for key in ("roll", "pitch", "yaw"):
        value = actual.get(key)
        expected = target.get(key)
        if value is None or expected is None or not all(
            math.isfinite(float(item)) for item in (value, expected)
        ):
            return None
        errors[key] = abs((float(value) - float(expected) + 180.0) % 360.0 - 180.0)
    return errors


def _attitude_error_detail(
    actual: dict[str, float] | None,
    target: dict[str, float],
    errors: dict[str, float] | None,
) -> str:
    if actual is None or errors is None:
        return "actual attitude unavailable"
    return (
        "actual R/P/Y "
        f"{actual['roll']:.1f}/{actual['pitch']:.1f}/{actual['yaw']:.1f} deg; "
        "target "
        f"{target['roll']:.1f}/{target['pitch']:.1f}/{target['yaw']:.1f} deg; "
        "error "
        f"{errors['roll']:.1f}/{errors['pitch']:.1f}/{errors['yaw']:.1f} deg"
    )


LEVEL_ATTITUDE_TOL_DEG = 3.0
PLACEMENT_ATTITUDE_TOL_DEG = 2.0


def _verify_level_attitude(
    actual: dict[str, float] | None,
    target: dict[str, float],
    *,
    stage: str = "outer retract",
) -> None:
    """Require a level tool outside the cage; yaw is aligned later."""
    errors = _attitude_errors(actual, target)
    if errors is None or any(
        errors[key] > LEVEL_ATTITUDE_TOL_DEG for key in ("roll", "pitch")
    ):
        detail = _attitude_error_detail(actual, target, errors)
        raise HTTPException(
            409,
            f"{stage}: gripper is not level within "
            f"{LEVEL_ATTITUDE_TOL_DEG:.0f} degrees ({detail}); "
            "gripper remains closed",
        )


def _verify_placement_attitude(
    actual: dict[str, float] | None,
    target: dict[str, float],
    *,
    stage: str = "placement",
) -> None:
    errors = _attitude_errors(actual, target)
    if errors is None or any(
        errors[key] > PLACEMENT_ATTITUDE_TOL_DEG for key in ("roll", "pitch", "yaw")
    ):
        detail = _attitude_error_detail(actual, target, errors)
        raise HTTPException(
            409,
            f"{stage}: placement attitude is not level/aligned within "
            f"{PLACEMENT_ATTITUDE_TOL_DEG:.0f} degrees "
            f"({detail}); gripper remains closed",
        )


def plan_pick_path(
    payload: PickPathIn,
    current_pose: dict[str, Any] | None,
    max_reach_m: float,
) -> dict[str, Any]:
    """Build a dry-run path: start -> [via] -> pre-gate -> gate -> [via] -> approach -> target."""
    if payload.corners is not None:
        prepared, corner_meta = _prepare_cage_corners(
            payload.corners, payload.ignored_corners
        )
        cage, fit_meta = _fit_cage_corners(
            prepared,
            payload.cage.opening_margin,
            payload.cage.clearance_m,
            payload.cage.front_top_open,
        )
        fit_meta.update(corner_meta)
        cage_source = "corners"
    else:
        cage = payload.cage
        fit_meta = None
        cage_source = "dimensions"

    if payload.start is not None:
        start = _pose_dict(payload.start.model_dump())
    elif current_pose is not None:
        start = _pose_dict(current_pose)
    else:
        raise HTTPException(409, "no current arm pose; provide start")

    via = _pose_dict(payload.via.model_dump()) if payload.via is not None else None
    home = _pose_dict(payload.home.model_dump()) if payload.home is not None else None
    target = _level_placement_pose(
        cage, _pose_dict(payload.target.model_dump()), payload.preserve_yaw
    )

    w, d, h, margin = cage.width, cage.depth, cage.height, cage.opening_margin
    target_local = _world_to_cage(cage, (target["x"], target["y"], target["z"]))
    start_local = _world_to_cage(cage, (start["x"], start["y"], start["z"]))
    target_inside = (
        -w / 2 <= target_local[0] <= w / 2
        and 0.0 <= target_local[1] <= d
        and 0.0 <= target_local[2] <= h
    )
    target_issues = _cage_target_issues(cage, target)
    if via is not None:
        via_local = _world_to_cage(cage, (via["x"], via["y"], via["z"]))
        via_inside = 0.0 < via_local[1] < d
        if via_inside:
            via = _level_placement_pose(cage, via)
    else:
        via_local = None
        via_inside = False
    start_inside = (
        0.0 < start_local[1] < d
        and -w / 2.0 <= start_local[0] <= w / 2.0
        and 0.0 <= start_local[2] <= h
    )

    warnings: list[str] = []
    warnings.append(
        "placement attitude locked: roll 0°, pitch 0°, "
        + ("yaw preserved from request" if payload.preserve_yaw else "yaw aligned with cage depth")
    )
    if fit_meta:
        warnings.extend(fit_meta.get("warnings", []))
    if start_inside:
        warnings.append(
            "start is inside the cage; use an outside start or the path will cross the frame"
        )
    if home is not None:
        home_local = _world_to_cage(cage, (home["x"], home["y"], home["z"]))
        home_inside = (
            0.0 < home_local[1] < d
            and -w / 2.0 <= home_local[0] <= w / 2.0
            and 0.0 <= home_local[2] <= h
        )
        if home_inside:
            warnings.append("home is inside the cage; move home outside the opening")
    if not target_inside:
        warnings.append("target is outside the taught cage; correct the target before execution")
    z_open_hi = h if cage.front_top_open else h - margin
    if w - 2 * margin <= 0 or z_open_hi - margin <= 0:
        warnings.append("opening_margin is too large for the cage dimensions")
    z_low = margin + cage.clearance_m
    z_high = z_open_hi - cage.clearance_m
    if z_high < z_low:
        z_high = z_low
        warnings.append("not enough vertical room for the gate clearance")

    yaw = math.radians(cage.yaw)
    normal_out = (math.sin(yaw), -math.cos(yaw), 0.0)
    gate_local = (
        min(
            max(target_local[0], -w / 2.0 + margin + cage.clearance_m),
            w / 2.0 - margin - cage.clearance_m,
        ),
        0.0,
        min(max(target_local[2], z_low), z_high),
    )
    gate_xyz = _cage_to_world(cage, gate_local)
    pre_gate_xyz = (
        gate_xyz[0] + normal_out[0] * payload.gate_clearance_m,
        gate_xyz[1] + normal_out[1] * payload.gate_clearance_m,
        gate_xyz[2],
    )

    approach_m = payload.approach_m
    if target_inside:
        max_inside = max(0.0, target_local[1] - 0.005)
        if approach_m > max_inside:
            approach_m = max_inside
            warnings.append(
                f"approach clamped to {approach_m * 1000:.0f} mm to stay inside the cage"
            )
    approach_xyz = (
        target["x"] + normal_out[0] * approach_m,
        target["y"] + normal_out[1] * approach_m,
        target["z"],
    )

    def oriented(
        xyz: tuple[float, float, float], reference: dict[str, float]
    ) -> dict[str, float]:
        return {
            "x": xyz[0],
            "y": xyz[1],
            "z": xyz[2],
            "roll": reference["roll"],
            "pitch": reference["pitch"],
            "yaw": reference["yaw"],
        }

    waypoints: list[tuple[str, dict[str, float]]] = [("start", start)]
    if home is not None:
        waypoints.append(("home", home))
    if via is not None and not via_inside:
        waypoints.append(("via", via))
    if target_inside:
        waypoints.append(("pre_gate", oriented(pre_gate_xyz, target)))
        waypoints.append(("gate", oriented(gate_xyz, target)))
    if via is not None and via_inside:
        waypoints.append(("via", via))
    if approach_m > 1e-6:
        waypoints.append(("approach", oriented(approach_xyz, target)))
    waypoints.append(("target", target))

    deduped: list[tuple[str, dict[str, float]]] = [waypoints[0]]
    for kind, pose in waypoints[1:]:
        previous = deduped[-1][1]
        if (
            math.dist(
                (previous["x"], previous["y"], previous["z"]),
                (pose["x"], pose["y"], pose["z"]),
            )
            > 1e-4
            or any(abs((pose[key] - previous[key] + 180) % 360 - 180) > 1e-4
                   for key in ("roll", "pitch", "yaw"))
        ):
            deduped.append((kind, pose))
    waypoints = deduped

    path_cage = _path_cage_for_payload(cage, payload.payload_radius_m)
    frame_segments = _cage_frame_segments(path_cage)
    blocking = bool(target_issues)
    points: list[dict[str, Any]] = []
    for kind, pose in waypoints:
        local = _world_to_cage(cage, (pose["x"], pose["y"], pose["z"]))
        in_box = (
            -w / 2.0 - 1e-6 <= local[0] <= w / 2.0 + 1e-6
            and -1e-6 <= local[1] <= d + 1e-6
            and -1e-6 <= local[2] <= h + 1e-6
        )
        zone = "gate" if abs(local[1]) <= 1e-6 else ("inside" if in_box else "outside")
        reach = math.hypot(pose["x"], pose["y"])
        issues: list[str] = list(target_issues) if kind == "target" else []
        if kind == "pre_gate" and reach < MIN_PRE_GATE_RADIUS_M - 1e-6:
            issues.append(
                f"pre_gate reach {reach:.3f} m is inside the "
                f"{MIN_PRE_GATE_RADIUS_M:.3f} m base exclusion zone; "
                "move or reteach the cage opening before grasping"
            )
        if reach > max_reach_m + 1e-6:
            issues.append(f"horizontal reach {reach:.3f} m > {max_reach_m:.3f} m")
        if not (MIN_POSE_Z_M <= pose["z"] <= MAX_POSE_Z_M):
            issues.append(
                f"z={pose['z']:.3f} m outside "
                f"[{MIN_POSE_Z_M:.2f}, {MAX_POSE_Z_M:.2f}] m"
            )
        if abs(local[1]) <= 1e-6 and not (
            -w / 2.0 + margin <= local[0] <= w / 2.0 - margin
            and margin <= local[2] <= z_open_hi
        ):
            issues.append("point on the opening plane is outside the opening rectangle")
        if issues:
            blocking = True
        points.append(
            {
                "kind": kind,
                "pose": pose,
                "zone": zone,
                "reach_m": round(reach, 4),
                "issues": issues,
            }
        )

    segments: list[dict[str, Any]] = []
    for index in range(len(waypoints) - 1):
        kind_a, a = waypoints[index]
        kind_b, b = waypoints[index + 1]
        p0 = (a["x"], a["y"], a["z"])
        p1 = (b["x"], b["y"], b["z"])
        length = math.dist(p0, p1)
        min_clearance, issues = _segment_cage_check(
            path_cage, frame_segments, p0, p1
        )
        if kind_a == "approach" and kind_b == "target":
            if abs(a["z"] - b["z"]) > 1e-6 or any(
                abs(a[key] - b[key]) > 1e-6 for key in ("roll", "pitch", "yaw")
            ):
                issues.append("final approach is not horizontal / attitude-locked")
        collision = bool(issues)
        if collision:
            blocking = True
        segments.append(
            {
                "from": kind_a,
                "to": kind_b,
                "length_m": round(length, 4),
                "min_clearance_m": (
                    round(min_clearance, 4) if math.isfinite(min_clearance) else None
                ),
                "collision": collision,
                "issues": issues,
            }
        )

    if via is not None and via_inside and target_inside and via_local[1] > target_local[1] + 0.005:
        warnings.append("via is deeper than target; the path backtracks toward the opening")

    opening_center_xyz = _cage_to_world(cage, (0.0, 0.0, h / 2.0))
    return {
        "ok": not blocking,
        "dry_run": True,
        "cage_source": cage_source,
        "cage_fit": fit_meta,
        "points": points,
        "segments": segments,
        "warnings": warnings,
        "actions": [
            {"at": "start", "action": "close_gripper"},
            {"at": "target", "action": "open_gripper"},
        ],
        "cage": cage.model_dump(),
        "opening": {
            "center": {
                "x": opening_center_xyz[0],
                "y": opening_center_xyz[1],
                "z": opening_center_xyz[2],
            },
            "gate": {"x": gate_xyz[0], "y": gate_xyz[1], "z": gate_xyz[2]},
            "normal_out": {
                "x": normal_out[0],
                "y": normal_out[1],
                "z": normal_out[2],
            },
            "width_m": round(w - 2 * margin, 4),
            "height_m": round(z_open_hi - margin, 4),
            "front_top_open": cage.front_top_open,
        },
        "limits": {
            "max_reach_m": max_reach_m,
            "z_min_m": MIN_POSE_Z_M,
            "z_max_m": MAX_POSE_Z_M,
        },
    }


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

    @app.get("/api/camera/status")
    def camera_status() -> dict[str, Any]:
        """Return the Jetson camera mode and last-frame ages."""
        base = CAMERA_STREAM_URL.rsplit("/", 1)[0]
        try:
            with urllib.request.urlopen(base + "/health", timeout=4) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise HTTPException(502, f"camera status unavailable: {exc}") from exc

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
        """Detect the QR in the exact browser frame and estimate its 3D pose."""
        if body.image_data_url:
            image = _decode_submitted_image(body.image_data_url)
        else:
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
        result = _detect_qr_roi(image, body.x, body.y, body.radius, roi)
        if not result.get("ok"):
            return result
        result["captured_at"] = body.captured_at
        arm_pose = body.arm_pose.model_dump() if body.arm_pose is not None else None
        result["arm_pose"] = arm_pose
        if arm_pose is not None:
            try:
                _require_stationary_arm(controller, arm_pose)
            except HTTPException as exc:
                result["arm_pose"] = None
                arm_pose = None
                result["pose_error"] = str(exc.detail)
        try:
            camera_pose = _estimate_qr_camera_pose(
                result["corners"], result["frame"]["w"], result["frame"]["h"]
            )
            result["camera_pose"] = camera_pose
            hand_eye = _load_hand_eye_calibration()
            if arm_pose is not None and hand_eye.get("camera_to_tool"):
                result["base_pose"] = _qr_base_pose(
                    arm_pose, camera_pose, hand_eye["camera_to_tool"]
                )
                result["target_pose"] = _qr_base_pose(
                    arm_pose,
                    camera_pose,
                    hand_eye["camera_to_tool"],
                    (
                        body.target_offset_x_m,
                        body.target_offset_y_m,
                        body.target_offset_z_m,
                    ),
                )
        except HTTPException as exc:
            result["pose_error"] = str(exc.detail)
        return result

    @app.get("/api/vision/hand_eye")
    def hand_eye_calibration() -> dict[str, Any]:
        return _load_hand_eye_calibration()

    @app.post("/api/vision/hand_eye/capture")
    def capture_hand_eye_point(body: HandEyeCaptureIn) -> dict[str, Any]:
        image = _decode_submitted_image(body.image_data_url)
        camera_pose = _estimate_charuco_camera_pose(image)
        arm_pose = body.arm_pose.model_dump()
        _require_stationary_arm(controller, arm_pose)
        sample = {
            "arm_pose": arm_pose,
            "camera_rvec": camera_pose["rvec"],
            "camera_tvec": camera_pose["tvec"],
            "captured_at": body.captured_at,
        }
        data = _load_hand_eye_calibration()
        data["samples"].append(sample)
        data["camera_to_tool"] = None
        data["last_capture"] = {
            "corners": camera_pose["corners"],
            "reprojection_error_px": camera_pose["reprojection_error_px"],
        }
        _save_hand_eye_calibration(data)
        return data

    @app.post("/api/vision/hand_eye/solve")
    def solve_hand_eye_calibration() -> dict[str, Any]:
        data = _load_hand_eye_calibration()
        data["camera_to_tool"] = _solve_hand_eye(data["samples"])
        _save_hand_eye_calibration(data)
        return data

    @app.delete("/api/vision/hand_eye")
    def reset_hand_eye_calibration() -> dict[str, Any]:
        data = {
            "samples": [],
            "camera_to_tool": None,
            "needed": 10,
            "qr_size_m": QR_CODE_SIZE_M,
        }
        _save_hand_eye_calibration(data)
        return data

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

    @app.post("/api/arm/linear")
    def arm_linear(body: LinearMoveIn) -> dict[str, Any]:
        """Move by dx/dy/dz with roll locked; pitch/yaw may be offset."""
        return controller.move_linear(
            body.dx, body.dy, body.dz, body.dyaw, body.dpitch
        )

    @app.post("/api/arm/jog_pose")
    def arm_jog_pose(body: LinearMoveIn) -> dict[str, Any]:
        """Stream a relative Cartesian offset through the firmware jog mode."""
        return controller.jog_pose(
            body.dx, body.dy, body.dz, body.dpitch, body.dyaw, body.dj6, body.droll
        )

    @app.post("/api/arm/jog_stream")
    def arm_jog_stream(body: JogStreamIn) -> dict[str, Any]:
        """Continuous velocity stream; the server paces the jog thread."""
        return controller.update_jog_stream(
            body.x, body.y, body.z, body.roll, body.pitch, body.yaw, body.j6, body.scale
        )

    @app.post("/api/arm/jog_stop")
    def arm_jog_stop() -> dict[str, Any]:
        return controller.stop_jog_stream()

    @app.post("/api/arm/stop")
    def arm_stop() -> dict[str, Any]:
        return controller.stop_motion()

    @app.post("/api/arm/cage_fit")
    def arm_cage_fit(body: CageFitIn) -> dict[str, Any]:
        """Fit the cage box to eight taught corners (no motion)."""
        prepared, meta = _prepare_cage_corners(body.points, body.ignored_corners)
        cage, fit = _fit_cage_corners(
            prepared,
            body.opening_margin,
            body.clearance_m,
            body.front_top_open,
        )
        fit.update(meta)
        return {"ok": True, "cage": cage.model_dump(), "fit": fit}

    @app.post("/api/arm/pick_path")
    def arm_pick_path(body: PickPathIn) -> dict[str, Any]:
        """Dry-run Cartesian path with cage frame clearance checks (no motion)."""
        snapshot = controller.snapshot()
        return plan_pick_path(body, snapshot["pose"], float(snapshot["max_reach_m"]))

    @app.post("/api/arm/execute_pick")
    def arm_execute_pick(body: ExecutePickIn) -> dict[str, Any]:
        """Close at the current pose, home, place at target, open (blocking)."""
        return controller.execute_pick_place(body)

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
