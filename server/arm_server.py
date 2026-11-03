#!/usr/bin/env python3
"""HTTP bridge between the PackTug control panel and a Niryo Ned2 arm.

Runs in the ``ned`` conda env (pyniryo 1.1.2) and serves:

  * ``/api/arm/*`` — small, rate-limited joint control surface
  * ``../dist``    — the built Vite control panel (SPA fallback)

The Ned2 has no hardware E-stop in this kit, so the bridge deliberately
limits moves: each request may change a joint by at most ``MAX_REQUEST_DEG``
(executed in ``MAX_STEP_DEG`` segments) and the arm speed is capped at
``MAX_VELOCITY`` percent. Motions are performed with PyNiryo's blocking
``move_joints``; keep a hand on the power switch.

Usage (from the control-panel folder):

    conda run --no-capture-output -n ned python server/arm_server.py --port 8020
    conda run --no-capture-output -n ned python server/arm_server.py --mock
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

DEFAULT_HOST = os.getenv("ARM_HOST", "10.10.10.10")
MAX_STEP_DEG = 15.0       # internal segment size for a move
MAX_REQUEST_DEG = 60.0    # largest joint change accepted per manual command
MAX_POSE_DEG = 180.0      # largest joint change when recalling a saved pose
MAX_VELOCITY = 5
MAX_VELOCITY_LIMIT = 20
GRIPPER_SPEED = 400
GRIPPER_CLOSE_TORQUE = 60
GRIPPER_HOLD_TORQUE = 30
POSES_FILE = Path(__file__).resolve().parent / "arm_poses.json"
SEQUENCES_FILE = Path(__file__).resolve().parent / "arm_sequences.json"
HOME_TRANSIT = "__home__"   # built-in safe transit: pyniryo move_to_home_pose()

# Ned2 joint limits, radians. J3 uses the URDF value; J5 uses the range the
# robot's MoveIt stack actually enforces (-100°..+55°), which is narrower than
# the marketing spec. A 0.5° margin keeps targets off the hard stops.
JOINT_LIMITS_RAD: list[tuple[float, float]] = [
    (-2.949, 2.949),
    (-1.83, 0.61),
    (-1.34390352404, 1.49225651046),
    (-2.089, 2.089),
    (-1.74532925199, 0.959931088597),
    (-2.53, 2.53),
]
JOINT_MARGIN_DEG = 0.5
JOINT_LIMITS_DEG = [
    [math.degrees(lo) + JOINT_MARGIN_DEG, math.degrees(hi) - JOINT_MARGIN_DEG]
    for lo, hi in JOINT_LIMITS_RAD
]

log = logging.getLogger("arm-server")


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
        self.updated_at: float | None = None
        self.tool: str | None = None
        self.gripper: str = "unknown"

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
                "max_step_deg": MAX_STEP_DEG,
                "max_request_deg": MAX_REQUEST_DEG,
                "max_pose_deg": MAX_POSE_DEG,
                "limits_deg": JOINT_LIMITS_DEG,
                "tool": self.tool,
                "gripper": self.gripper,
                "busy": self.busy,
                "error": self.last_error,
                "updated_at": self.updated_at,
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
                    self.last_error = None
                    self.updated_at = time.time()
                log.info("mock arm connected (%s)", host)
                return self.snapshot()

            from pyniryo import NiryoRobot

            log.info("connecting to Ned2 at %s ...", host)
            robot = NiryoRobot(host)
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
                with self._state:
                    self.connected = True
                    self.host = host
                    self.tool = tool_name
                    self.gripper = "unknown"
                    self.last_error = None
            except Exception:
                with self._state:
                    self._robot = None
                try:
                    robot.close_connection()
                except Exception:
                    pass
                raise
            log.info("Ned2 connected at %s", host)
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

    # ---------- reads ----------

    def _read_locked(self) -> None:
        if self.mock:
            return
        robot = self._robot
        if robot is None:
            raise ConnectionError("not connected")
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

    def _check_tool_locked(self) -> None:
        with self._state:
            if not self.connected:
                raise HTTPException(409, "arm is not connected")
            if self.learning_mode:
                raise HTTPException(409, "motors are released — enable motors first")
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

    def _execute_move_locked(self, current: list[float], target: list[float]) -> None:
        """Run the move in <=MAX_STEP_DEG segments so a slider drag is smooth."""
        max_delta = max(abs(t - c) for c, t in zip(current, target))
        segments = max(1, math.ceil(max_delta / MAX_STEP_DEG))
        for i in range(1, segments + 1):
            alpha = i / segments
            waypoint = [c + (t - c) * alpha for c, t in zip(current, target)]
            try:
                self._robot.move_joints([math.radians(v) for v in waypoint])
            except Exception as exc:
                raise RuntimeError(
                    f"segment {i}/{segments} failed at joints "
                    f"{[round(v, 1) for v in waypoint]}: {exc}"
                ) from exc
            with self._state:
                self.joints_deg = waypoint
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
            self._validate_target(target_deg, current, max_delta)
            if self.mock:
                time.sleep(0.15)
                with self._state:
                    self.joints_deg = [float(v) for v in target_deg]
                    self.updated_at = time.time()
            else:
                self._execute_move_locked(current, target_deg)
                self._read_locked()
            with self._state:
                self.last_error = None
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("motion failed")
            with self._state:
                self.last_error = f"{type(exc).__name__}: {exc}"
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
                if self.mock:
                    time.sleep(0.2)
                    with self._state:
                        self.joints_deg = [0.0, -15.0, 20.0, 0.0, 0.0, 0.0]
                        self.updated_at = time.time()
                else:
                    self._robot.move_to_home_pose()
                    self._read_locked()
                with self._state:
                    self.last_error = None
            except HTTPException:
                raise
            except Exception as exc:
                log.exception("home move failed")
                with self._state:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                raise HTTPException(502, self.last_error) from exc
            finally:
                with self._state:
                    self.busy = False
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
                    if self._robot.get_learning_mode():
                        self._robot.set_learning_mode(False)
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
                    self._robot.set_learning_mode(True)
                    with self._state:
                        self.learning_mode = True
                        self.updated_at = time.time()
                else:
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

    def _gripper_action(self, close: bool) -> dict[str, Any]:
        with self._motion:
            self._check_tool_locked()
            with self._state:
                self.busy = True
            try:
                if self.mock:
                    time.sleep(0.2)
                elif close:
                    self._robot.close_gripper(
                        speed=GRIPPER_SPEED,
                        max_torque_percentage=GRIPPER_CLOSE_TORQUE,
                        hold_torque_percentage=GRIPPER_HOLD_TORQUE,
                    )
                else:
                    self._robot.open_gripper(speed=GRIPPER_SPEED)
                with self._state:
                    self.gripper = "closed" if close else "open"
                    self.last_error = None
                    self.updated_at = time.time()
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

    def open_gripper(self) -> dict[str, Any]:
        return self._gripper_action(False)

    def close_gripper(self) -> dict[str, Any]:
        return self._gripper_action(True)

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

    def apply_pose(self, name: str) -> dict[str, Any]:
        with self._poses_lock:
            poses = self._load_poses_locked()
            pose = next((p for p in poses if p["name"] == name), None)
        if pose is None:
            raise HTTPException(404, f"pose not found: {name}")
        with self._motion:
            self._check_motion_locked()
            current = self._current_joints_locked()
            try:
                return self._move_locked(pose["joints_deg"], current, MAX_POSE_DEG)
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

    @app.get("/api/arm/status")
    def arm_status() -> dict[str, Any]:
        return controller.status()

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

    @app.post("/api/arm/tool/scan")
    def arm_tool_scan() -> dict[str, Any]:
        return controller.scan_tool()

    @app.post("/api/arm/home")
    def arm_home() -> dict[str, Any]:
        return controller.move_home()

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
    def arm_apply_pose(name: str) -> dict[str, Any]:
        return controller.apply_pose(name)

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
    parser = argparse.ArgumentParser(description="Ned2 arm bridge + control-panel server")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP listen address (use 0.0.0.0 to expose on LAN)")
    parser.add_argument("--port", type=int, default=8020, help="HTTP listen port")
    parser.add_argument("--arm-host", default=DEFAULT_HOST, help="Ned2 IP (10.10.10.10 WiFi / 169.254.200.200 Ethernet)")
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
