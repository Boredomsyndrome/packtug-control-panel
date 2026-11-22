@echo off
setlocal
cd /d "%~dp0"

set "CAMERA_STREAM_URL=http://127.0.0.1:8080/stream"
set "CAMERA_DEPTH_URL=http://127.0.0.1:8080/depth"
set "CAMERA_MODE_URL=http://127.0.0.1:8080/mode"
set "CAMERA_SNAPSHOT_URL=http://127.0.0.1:8080/snapshot"
set "NO_PROXY=127.0.0.1,localhost,<jetson-ip>,100.64.0.0/10"
set "HTTP_PROXY="
set "HTTPS_PROXY="

echo Starting PackTug arm bridge on http://127.0.0.1:8020 ...
echo Press Ctrl+C to stop.
echo.

python server\arm_server.py --port 8020

echo.
echo arm_server stopped. Press any key to close.
pause >nul
