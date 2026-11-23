"""Environment configuration for the hub — pure Python, no framework.

v3.8: the robot plane moved from Django to FastAPI; this module is the one
place that reads os.environ / .env files. Values and parsing semantics are
byte-compatible with the old webctl/settings.py (WEBCTL_ROBOTS registry,
clamps, frame caps), so deploy env files keep working unchanged.

Hub runtime secrets live in .env.server (gitignored, 600) — the tracked
.env is the VITE build config and must never carry tokens (CS-I4).
"""
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv

# App-level INFO visibility: uvicorn's CLI configures only its OWN loggers,
# so robot-plane events (robot.api / robot.hub / robot.ws) were silently
# dropped — ws lifecycle was invisible exactly when you needed it. Standard
# guard: basicConfig iff nothing has configured root logging yet.
logging.basicConfig(
    level=os.getenv("WEBCTL_LOG_LEVEL", "INFO").upper(),
    format="%(levelname)s %(name)s: %(message)s",
)

BASE_DIR = Path(__file__).resolve().parent.parent          # backend/
PROJECT_ROOT = BASE_DIR.parent                              # repo root

load_dotenv(PROJECT_ROOT / ".env.server")
load_dotenv(PROJECT_ROOT / ".env")

DEBUG = os.getenv("PACKTUG_DEBUG", "true").lower() == "true"

# Robots are PURE CLIENTS. WEBCTL_ROBOTS registers the fleet:
# "robot_id:token,other_id:other_token". Tokens are per-robot secrets the
# robot sends as HTTP Basic (username = robot id) or ws ?token=. An empty
# token disables auth for that robot (dev).
WEBCTL_ROBOTS = {}
for _entry in os.getenv("WEBCTL_ROBOTS", "s2000-1:").split(","):
    _entry = _entry.strip()
    if _entry:
        _rid, _, _tok = _entry.partition(":")
        _rid = _rid.strip()
        if _rid and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", _rid):
            WEBCTL_ROBOTS[_rid] = _tok.strip()

MAX_LIN = float(os.getenv("MAX_LIN", "0.5"))                    # m/s
MAX_ROT = float(os.getenv("MAX_ROT", "0.7"))                    # rad/s
CMD_TIMEOUT = float(os.getenv("CMD_TIMEOUT", "0.5"))            # s
MAX_FRAME = int(os.getenv("MAX_FRAME", str(2 * 1024 * 1024)))   # cam JPEG cap
