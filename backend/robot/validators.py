"""Input validation for the hub API — pydantic v2 models (v3.8: FastAPI port).

Semantics copied 1:1 from the old DRF serializers, INCLUDING the
{"field": ["message", ...]} error shape the frontend renders:
Input validation (VelocityCommand) is the first safety layer;
the hub clamps again to MAX_LIN/MAX_ROT regardless of what passes here.
Telemetry guards the robot uplink (numbers only, no NaN/inf).
"""
import math
import re
from typing import Annotated, Any, Literal, Optional

from pydantic import (BaseModel, ConfigDict, Field, StringConstraints,
                      ValidationError, model_validator)

NAME_RE = r"^[A-Za-z0-9_-]{1,32}$"
NAME_MSG = "only [A-Za-z0-9_-], 1-32 chars"
NameStr = Annotated[str, StringConstraints(pattern=NAME_RE, max_length=32)]
Ranges = Annotated[float, Field(ge=0.0, le=1000.0)]

ActionKind = Literal["map_on", "map_off", "map_save", "nav_on", "route",
                     "nav_cancel", "nav_off", "record", "align",
                     "delete_map", "delete_waypoint", "clear_waypoints"]


def _finite(v):
    if isinstance(v, float) and not math.isfinite(v):
        raise ValueError("must be a finite number")
    return v


# ---------------------------------------------------------------------------

class VelocityCommand(BaseModel):
    """POST body for a velocity command: {"vx": m/s, "wz": rad/s}."""
    model_config = ConfigDict(extra="ignore")
    vx: float = Field(default=0.0, ge=-1e6, le=1e6)
    wz: float = Field(default=0.0, ge=-1e6, le=1e6)

    @model_validator(mode="after")
    def _check_finite(self):
        _finite(self.vx)
        _finite(self.wz)
        return self


class TelemetryCommand(BaseModel):
    """Uplink body for POST /robotctl/<id>/telemetry (and the ws frame)."""
    model_config = ConfigDict(extra="ignore")
    battery_v: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    applied_vx: float = Field(default=0.0, ge=-1e3, le=1e3)
    applied_wz: float = Field(default=0.0, ge=-1e3, le=1e3)
    watchdog_trips: int = Field(default=0, ge=0, le=10 ** 9)
    cam_ok: bool = False
    scan_ok: bool = False
    odom: Optional[Annotated[list[float],
                             Field(min_length=3, max_length=3)]] = None  # v3.7
    ros_topics: Optional[dict] = None
    nav: Optional[dict] = None          # v3.3 nav state block

    @model_validator(mode="after")
    def _check_finite(self):
        for value in (self.battery_v, self.applied_vx, self.applied_wz):
            _finite(value)
        if self.odom and not all(math.isfinite(v) for v in self.odom):
            raise ValueError("odom values must be finite")
        return self

    def to_hub(self) -> dict:
        """Keys present EXACTLY like DRF's validated_data: fields without a
        serializer default (battery_v/odom/ros_topics/nav) are ABSENT when
        unset — hub.set_telemetry treats an absent 'nav' differently from
        an empty dict."""
        d = self.model_dump()
        for k in ("battery_v", "odom", "ros_topics", "nav"):
            if d.get(k) is None:
                d.pop(k, None)
        return d


class ScanPayload(BaseModel):
    """One packed sensor_msgs/LaserScan (ws text frame or HTTP POST).

    Ranges are already downsampled + sanitised robot-side; we STILL
    hard-validate here — a robot is untrusted input. 0.0 = no return (gap)."""
    model_config = ConfigDict(extra="ignore")
    frame: str = Field(default="laser", max_length=64)
    topic: str = Field(default="", max_length=64)
    amin: float = Field(ge=-20.0, le=20.0)
    amax: float = Field(ge=-20.0, le=20.0)
    ainc: float = Field(ge=-1.0, le=1.0)
    rmin: float = Field(ge=0.0, le=100.0)
    rmax: float = Field(ge=0.0, le=1000.0)
    ranges: Annotated[list[Ranges], Field(min_length=1, max_length=4096)]

    @model_validator(mode="after")
    def _check(self):
        if self.amax <= self.amin:
            raise ValueError("amax must exceed amin")
        for v in self.ranges:
            _finite(v)
        return self


class NavActionBody(BaseModel):
    """POST body for /api/robot/<id>/nav — the browser-side nav remote
    (docs/NAV.md). We validate names + shapes HERE; the robot re-validates
    defensively, and only 'route' with a resolvable pose list can move it."""
    model_config = ConfigDict(extra="ignore")
    action: ActionKind
    name: Optional[NameStr] = None
    map: Optional[NameStr] = None
    # cap bounds one route POST's payload; sized for RECORDED paths (≈1 m
    # spacing → 256 pts ≈ 250 m of route). 64 was fine for hand-picked
    # waypoints but a "queue all" of a taught path must not 422.
    waypoints: Optional[Annotated[list[NameStr], Field(max_length=256)]] = None

    @model_validator(mode="after")
    def _cross(self):
        a = self.action
        if a in ("nav_on", "delete_map") and not self.map:
            raise ValueError({"map": f"required for {a}"})
        if a in ("route", "align", "delete_waypoint") and not self.waypoints:
            raise ValueError({"waypoints": f"required for {a}"})
        if a == "align" and len(self.waypoints or []) != 1:
            raise ValueError({"waypoints": "align takes exactly one point"})
        return self


# ---------------------------------------------------------------------------

_MSGS = {
    "missing": "This field is required.",
    "float_parsing": "A valid number is required.",
    "int_parsing": "A valid integer is required.",
    "float_type": "A valid number is required.",
    "int_type": "A valid integer is required.",
    "bool_type": "Must be a valid boolean.",
    "list_type": "A valid list is required.",
    "dict_type": "A valid dictionary is required.",
    "string_type": "A valid string is required.",
}


def _drf_message(err: Any) -> str:
    et = err["type"]
    ctx = err.get("ctx") or {}
    if et in _MSGS:
        return _MSGS[et]
    if et == "value_error":
        raw = str(err.get("msg", "")).strip()
        return raw.removeprefix("Value error, ") or "Invalid value."
    if et == "less_than_equal":
        return f"Ensure this value is less than or equal to {ctx.get('le')}."
    if et == "greater_than_equal":
        return f"Ensure this value is greater than or equal to {ctx.get('ge')}."
    if et == "string_too_long" or et == "list_too_long":
        unit = "characters" if et == "string_too_long" else "items"
        return f"Ensure this field has no more than {ctx.get('max_length')} {unit}."
    if et == "string_too_short" or et == "list_too_short":
        unit = "characters" if et == "string_too_short" else "elements"
        return f"Ensure this field has at least {ctx.get('min_length')} {unit}."
    if et == "string_pattern_mismatch":
        return NAME_MSG
    if et == "literal_error":
        return f"{err.get('input')!r} is not a valid choice."
    return str(err.get("msg", "Invalid value."))


_XERR = re.compile(r"^\{'(\w+)': '(.*)'\}$")


def parse(model, body):
    """Validate `body` (already-parsed JSON) against `model`.

    Returns (validated_dict, None) or (None, errors) where errors mimics
    DRF's shape: {"field": ["message", ...]}."""
    if not isinstance(body, dict):
        return None, {"detail": ["A valid JSON object is required."]}
    try:
        obj = model.model_validate(body)
    except ValidationError as exc:
        errors: dict = {}
        for err in exc.errors():
            loc = tuple(str(x) for x in err["loc"])
            key = loc[0] if loc else "detail"
            msgs = errors.setdefault(key, [])
            m = _drf_message(err)
            if m not in msgs:
                msgs.append(m)
        # model_validator cross-field rules raise ValueError({...}) so the
        # client gets per-field messages; pydantic stringified that into
        # "detail" — rebuild the intended {field: msg} shape.
        for m in list(errors.get("detail", [])):
            mx = _XERR.match(m)
            if mx:
                errors.setdefault(mx.group(1), []).append(mx.group(2))
                errors["detail"].remove(m)
        if "detail" in errors and not errors["detail"]:
            del errors["detail"]
        return None, errors
    return obj.model_dump(), None
