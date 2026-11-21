"""Django settings for the wheeltec-webctl robot control server.

Designed to run from any computer on the same LAN/tailnet as the robot;
everything robot-specific comes from env / .env (see ../.env.example).
"""
import os
import re
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent   # backend/
PROJECT_ROOT = BASE_DIR.parent                      # wheeltec-webctl/

# Hub runtime secrets live in .env.server (gitignored, 600) — the tracked
# .env is the VITE build config and must never carry tokens (CS-I4).
load_dotenv(PROJECT_ROOT / ".env.server")
load_dotenv(PROJECT_ROOT / ".env")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-insecure-key-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = [h for h in os.getenv("ALLOWED_HOSTS", "*").split(",") if h]

# Pull-mode robot config. Robots are PURE CLIENTS: they long-poll this
# server for commands and POST telemetry/camera up. Nothing here points at
# a robot; the robot side is configured in robotctl/client.py env instead.
#
# WEBCTL_ROBOTS registers the fleet: "robot_id:token,other_id:other_token".
# Tokens are per-robot secrets the robot sends as HTTP Basic
# (username = robot id). An empty token disables auth for that robot (dev).
WEBCTL_ROBOTS = {}
for _entry in os.getenv("WEBCTL_ROBOTS", "s2000-1:").split(","):
    _entry = _entry.strip()
    if _entry:
        _rid, _, _tok = _entry.partition(":")
        _rid = _rid.strip()
        if _rid and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", _rid):
            WEBCTL_ROBOTS[_rid] = _tok.strip()

MAX_LIN = float(os.getenv("MAX_LIN", "0.5"))             # m/s
MAX_ROT = float(os.getenv("MAX_ROT", "0.7"))             # rad/s
CMD_TIMEOUT = float(os.getenv("CMD_TIMEOUT", "0.5"))     # s; command staleness
MAX_FRAME = int(os.getenv("MAX_FRAME", str(2 * 1024 * 1024)))  # cam JPEG cap

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "robot",
]

# JSON-only API, no DRF auth (robot plane enforces per-robot tokens per
# request; browser plane is network-trusted by design since v3.2),
# no browsable HTML API (robot control endpoint should not render forms).
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # no django.contrib.auth in INSTALLED_APPS — avoid DRF importing AnonymousUser
    "UNAUTHENTICATED_USER": None,
}

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "webctl.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [PROJECT_ROOT / "dist"],
        # APP_DIRS True: drf_spectacular's redoc template ships in its own
        # app dir; without this /api/redoc 500s (TemplateDoesNotExist)
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

WSGI_APPLICATION = "webctl.wsgi.application"

# No database needed: the server is stateless telemetry + command relay.
DATABASES = {}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Fused site (2026-11-21): the panel SPA builds to repo-root dist/ and is
# the site root; the AGV console builds to dist/agv/ (served at /agv/).
# WhiteNoise serves both; index routing lives in robot/views.index_view.
WHITENOISE_ROOT = PROJECT_ROOT / "dist"

SPECTACULAR_SETTINGS = {
    "TITLE": "WHEELTEC WebCtl API",
    "DESCRIPTION": "Hub API: robot fleet, velocity/estop, LiDAR frames and "
                   "the nav remote (docs/NAV.md). Browser plane is "
                   "network-trusted (LAN/VPN); robot plane keeps per-robot "
                   "tokens.",
    "VERSION": "3.4.2",
    "SERVE_INCLUDE_SCHEMA": False,
    "SORT_OPERATIONS": False,
}

# Browser password REMOVED 2026-11-04 (v3.2): access control is the network
# (VPN/LAN trust). Robot plane still authenticates per-robot tokens in
# WEBCTL_ROBOTS — unaffected.
