"""Django settings for the wheeltec-webctl robot control server.

Designed to run from any computer on the same LAN/tailnet as the robot;
everything robot-specific comes from env / .env (see ../.env.example).
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent   # backend/
PROJECT_ROOT = BASE_DIR.parent                      # wheeltec-webctl/

load_dotenv(PROJECT_ROOT / ".env")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-insecure-key-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = [h for h in os.getenv("ALLOWED_HOSTS", "*").split(",") if h]

# Robot connection config (used by robot.bridge and views)
ROBOT_WS_URL = os.getenv("ROBOT_WS_URL", "ws://192.168.0.100:9090")
CAMERA_BASE_URL = os.getenv("CAMERA_BASE_URL", "http://192.168.0.100:8080")
CAMERA_TOPIC = os.getenv("CAMERA_TOPIC", "/image_raw")
BATTERY_TOPIC = os.getenv("BATTERY_TOPIC", "")          # "" = auto-try candidates
CMD_VEL_TOPIC = os.getenv("CMD_VEL_TOPIC", "/cmd_vel")
MAX_LIN = float(os.getenv("MAX_LIN", "0.5"))             # m/s
MAX_ROT = float(os.getenv("MAX_ROT", "0.7"))             # rad/s
CMD_TIMEOUT = float(os.getenv("CMD_TIMEOUT", "0.5"))     # s; watchdog stop

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "rest_framework",
    "robot",
]

# JSON-only API, no DRF auth (site-wide Basic Auth middleware handles it),
# no browsable HTML API (robot control endpoint should not render forms).
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    # no django.contrib.auth in INSTALLED_APPS — avoid DRF importing AnonymousUser
    "UNAUTHENTICATED_USER": None,
}

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "robot.middleware.BasicAuthMiddleware",   # no-op unless WEBCTL_AUTH_USER set
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "webctl.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [PROJECT_ROOT / "frontend" / "dist"],
        "APP_DIRS": False,
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

# Serve the built React app (frontend/dist) directly: /assets/* etc.
WHITENOISE_ROOT = PROJECT_ROOT / "frontend" / "dist"

# HTTP Basic auth (optional, enable via env before any internet exposure)
WEBCTL_AUTH_USER = os.getenv("WEBCTL_AUTH_USER", "")
WEBCTL_AUTH_PASS = os.getenv("WEBCTL_AUTH_PASS", "")
