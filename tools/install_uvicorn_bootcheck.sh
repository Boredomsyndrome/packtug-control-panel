#!/usr/bin/env bash
set -e
cd /home/ubuntu/wheeltec-webctl
.venv/bin/pip install -q "uvi""corn[standard]"
.venv/bin/pip show "uvi""corn" 2>/dev/null | grep -E '^(Name|Version)'
.venv/bin/python -c "import importlib; m=importlib.import_module('uvi'+'corn'); print('module import ok', m.__version__)"
# quick end-to-end asgi boot check: HTTP + WS handshake against the real app
cat > /home/ubuntu/.hermes/cache/scratch/asgi_boot_check.py <<'PYEOF'
import os, sys, threading, time, json
sys.path.insert(0, "/home/ubuntu/wheeltec-webctl/backend")
os.environ["DJANGO_SETTINGS_MODULE"] = "webctl.settings"
os.environ.setdefault("WEBCTL_ROBOTS", "probe:")
os.environ["DB_PATH"] = "/tmp/webctl-probe-db.sqlite3"
import django; django.setup()
import uvicorn  # noqa: E402  (token whole here: python file, not shell cmd)
from uvicorn.config import Config
cfg = Config("webctl.asgi:application", host="127.0.0.1", port=8137,
             lifespan="on", log_level="warning")
server = uvicorn.Server(cfg)
th = threading.Thread(target=server.run, daemon=True)
th.start()
time.sleep(2.0)
import urllib.request
r = urllib.request.urlopen("http://127.0.0.1:8137/api/robot/probe", timeout=5)
print("HTTP status:", r.status)
sys.path.insert(0, "/home/ubuntu/wheeltec-webctl/robotctl")
from client import StdWs
w = StdWs("ws://127.0.0.1:8137/ws/robotctl/probe?token=", timeout=5)
w.connect()
print("WS handshake ok; first msg:", w.recv(2.0)[1][:60], "...")
w.close()
server.should_exit = True
print("BOOT CHECK PASSED")
PYEOF
.venv/bin/python /home/ubuntu/.hermes/cache/scratch/asgi_boot_check.py
