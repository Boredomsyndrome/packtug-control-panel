"""File-based persistence for the nav plane: named waypoints + saved maps.

The hub deliberately has no database (settings has none, the single-owner
flock process owns the whole state), so waypoints live in one small JSON
file and map images under .data/. Writes are lock-guarded + atomic
(tempfile + os.replace — same pattern as the dry-run velocity sink).

WEBCTL_DATA_DIR overrides the root (integration harness uses a temp dir).
"""
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

from django.conf import settings

BASE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_lock = threading.Lock()
MAX_PGM = 8 * 1024 * 1024
MAX_YAML = 4096


def _root() -> Path:
    override = os.environ.get("WEBCTL_DATA_DIR")
    return Path(override) if override else Path(settings.PROJECT_ROOT) / ".data"


def _store_file() -> Path:
    return _root() / "waypoints.json"


def _load() -> dict:
    try:
        with open(_store_file(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    path = _store_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def valid_name(name) -> str | None:
    return name if isinstance(name, str) and BASE_NAME.fullmatch(name) else None


# ------------------------------------------------------------ waypoints ----
def list_waypoints(robot_id: str) -> dict:
    with _lock:
        return dict(_load().get(robot_id, {}).get("waypoints", {}))


def save_waypoint(robot_id: str, name: str, x: float, y: float,
                  yaw: float, frame: str = "map") -> dict:
    vname = valid_name(name)
    if vname is None:
        raise ValueError("waypoint name must match [A-Za-z0-9_-]{1,32}")
    entry = {"x": round(float(x), 4), "y": round(float(y), 4),
             "yaw": round(float(yaw), 4), "frame": str(frame)[:16],
             "created": round(time.time(), 2)}
    with _lock:
        data = _load()
        data.setdefault(robot_id, {}).setdefault("waypoints", {})[vname] = entry
        _save(data)
    return entry


def delete_waypoint(robot_id: str, name: str) -> bool:
    with _lock:
        data = _load()
        wps = data.get(robot_id, {}).get("waypoints", {})
        if name in wps:
            del wps[name]
            _save(data)
            return True
        return False


def clear_waypoints(robot_id: str) -> bool:
    """Delete ALL saved waypoints of one robot (UI 'clear path'). True only
    if something actually existed, so a double-tap 404s instead of lying."""
    with _lock:
        data = _load()
        wps = data.get(robot_id, {}).get("waypoints", {})
        if not wps:
            return False
        data[robot_id]["waypoints"] = {}
        _save(data)
        return True


# ----------------------------------------------------------------- maps ----
def maps_dir(robot_id: str) -> Path:
    return _root() / "maps" / robot_id


def list_maps(robot_id: str) -> list:
    out = []
    d = maps_dir(robot_id)
    try:
        for pgm in sorted(d.glob("*.pgm")):
            yaml = pgm.with_suffix(".yaml")
            st = pgm.stat()
            out.append({"name": pgm.stem, "bytes": st.st_size,
                        "saved": round(st.st_mtime, 2),
                        "yaml": yaml.exists()})
    except OSError:
        pass
    return out


def save_map(robot_id: str, name: str, pgm: bytes, yaml: str) -> None:
    vname = valid_name(name)
    if vname is None:
        raise ValueError("map name must match [A-Za-z0-9_-]{1,32}")
    if not isinstance(pgm, (bytes, bytearray)) or not pgm.startswith(b"P"):
        raise ValueError("pgm must be a PBM-family image starting with 'P'")
    if len(pgm) > MAX_PGM:
        raise ValueError(f"pgm exceeds {MAX_PGM} B")
    if not isinstance(yaml, str) or len(yaml.encode()) > MAX_YAML:
        raise ValueError(f"yaml must be <= {MAX_YAML} chars")
    if "image:" not in yaml:
        raise ValueError("yaml is not a map_server yaml (no image: key)")
    d = maps_dir(robot_id)
    d.mkdir(parents=True, exist_ok=True)
    for path, blob in ((d / f"{vname}.pgm", bytes(pgm)),
                       (d / f"{vname}.yaml", yaml.encode("utf-8"))):
        fd, tmp = tempfile.mkstemp(dir=str(d))
        with os.fdopen(fd, "wb") as fh:
            fh.write(blob)
        os.replace(tmp, path)


def merge_maps(uploaded: list, local_names) -> list:
    """Hub list = uploaded maps + robot-local maps whose upload never made
    it (uploaded=False). nav_on reads the ROBOT's own files, so the UI must
    still offer these — otherwise one lost upload = unreachable nav plane
    (corridor incident, 2026-11-11)."""
    out = [dict(m, uploaded=True) for m in uploaded]
    have = {m["name"] for m in out}
    for nm in local_names or []:
        if isinstance(nm, str) and nm not in have:
            out.append({"name": nm, "bytes": 0, "saved": 0,
                        "yaml": False, "uploaded": False})
    return out


def delete_map(robot_id: str, name: str) -> bool:
    vname = valid_name(name)
    if vname is None:
        return False
    gone = False
    d = maps_dir(robot_id)
    for ext in (".pgm", ".yaml"):
        try:
            (d / f"{vname}{ext}").unlink()
            gone = True
        except OSError:
            pass
    return gone


def map_file(robot_id: str, name: str, ext: str):
    """Path of a saved map artifact (ext: 'pgm' | 'yaml'); None if invalid
    or missing. Path NEVER escapes maps_dir (names are regex-locked)."""
    vname = valid_name(name)
    if vname is None or ext not in ("pgm", "yaml"):
        return None
    path = maps_dir(robot_id) / f"{vname}.{ext}"
    return path if path.is_file() else None
