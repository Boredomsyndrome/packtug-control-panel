# wheeltec-webctl — WHEELTEC S2000 browser control (Django 6.1 + DRF + React + rosbridge)

Control the WHEELTEC S2000 brushless robot (ROS2 SBC on board) from a web page,
over LAN or internet. Django backend holds ONE authenticated rosbridge
WebSocket connection to the robot and enforces safety; the React page talks
only to Django.

## API (DRF ViewSet + serializers)

- `GET  /api/robot` — status/telemetry snapshot (`robot/serializers.py: RobotStatusSerializer`)
- `POST /api/robot/cmd` `{"vx": m/s, "wz": rad/s}` — validated by `VelocityCommandSerializer`, clamped by bridge
- `POST /api/robot/stop` — zero velocity AND clear latched e-stop
- `POST /api/robot/estop` — latch zero until next explicit command
- `GET  /cam` — MJPEG proxy (plain Django streaming view; DRF renderers would break the endless stream)

```
browser (phone/laptop)                control computer (anywhere)          robot SBC
   │  HTTPS + Basic Auth                 Django :8000                      ROS2
   │  WASD/gamepad → /api/cmd ~10 Hz   ┌──────────────────┐   WiFi/LAN   ┌─────────────────┐
   ├──────────────────────────────────▶│ rosbridge client  ├─────────────▶│ rosbridge :9090 │
   │   MJPEG <img src=/cam>            │  • clamp 0.5 m/s  │              │  → /cmd_vel     │
   └◀───────────────────────────────────┤  • watchdog 0.5 s │              │  wheeltec node  │
              built React page         │  • e-stop latch   │              │  → STM32 UART   │ → ESCs → hub motors
                                       │  • camera proxy   │◀─────────────┤ web_video :8080 │
                                       └──────────────────┘              └─────────────────┘
```

Safety model (non-negotiable for brushless):
- velocities clamped server-side (`MAX_LIN`, `MAX_ROT`)
- 10 Hz publish; nothing fresh for `CMD_TIMEOUT` s → zero Twist (watchdog)
- big red e-stop: latched zero until an explicit new command
- one process owns the robot connection (flock); others return 503
- never port-forward 9090/8080 — only Django's port goes through HTTPS/VPN

## A. Robot side (SBC, run once + on boot)

Full step-by-step (power-on safety → hotspot → SSH → ROS2 → autostart →
first drive → demo checklist): see **ROBOT_SETUP.md** in this folder.

Wheeltec ROS2 images (Humble). For ROS1 Noetic kits, same app works — the
protocol differs slightly; see skill `wheeltec-s2000-wifi-control`.

```bash
sudo apt install ros-humble-rosbridge-suite ros-humble-web-video-server
# start the normal wheeltec stack first (bringup), then:
ros2 launch rosbridge_server rosbridge_websocket_launch.xml &   # ws :9090
ros2 run web_video_server web_video_server --ros-args -p port:=8080 &
```

Optional systemd unit `/etc/systemd/system/wheeltec-web.service`:

```ini
[Unit]
Description=rosbridge + web_video_server for wheeltec-webctl
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Environment=ROS_DOMAIN_ID=0
ExecStartPre=/bin/sleep 20
ExecStart=/bin/bash -lc 'source /opt/ros/humble/setup.bash && ros2 launch rosbridge_server rosbridge_websocket_launch.xml'
Restart=always

[Install]
WantedBy=multi-user.target
```

(find the robot's camera topic with `ros2 topic list`; web_video_server serves it at
`http://ROBOT_IP:8080/stream?topic=/<topic>`)

## B. Control computer (the "other computer")

Requirements: Python ≥3.10 (edit `pyproject.toml` requires-python if older),
Node 18+ (only when rebuilding the UI), Linux/macOS.

```bash
# copy this whole folder to the new machine, then:
cd wheeltec-webctl
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cd frontend && npm ci && npm run build && cd ..
cp .env.example .env    # then edit it (see below)
```

`.env` keys that matter:

| key | meaning |
|---|---|
| `ROBOT_WS_URL` | `ws://<robot-ip>:9090` (or `wss://` via Tailscale) |
| `CAMERA_BASE_URL` | `http://<robot-ip>:8080` |
| `CAMERA_TOPIC` | leave empty to auto-match; force with e.g. `/camera/image_raw` |
| `BATTERY_TOPIC` | wheeltec ROS2 default publishes `/PowerVoltage`; empty = try both |
| `MAX_LIN` / `MAX_ROT` | speed clamp (start small: 0.5 / 0.7) |
| `CMD_TIMEOUT` | watchdog seconds (0.5) |
| `WEBCTL_AUTH_USER` / `WEBCTL_AUTH_PASS` | Basic Auth — REQUIRED for internet |
| `DJANGO_SECRET_KEY` | random string (`python3 -c "import secrets;print(secrets.token_urlsafe(40))"`) |
| `ALLOWED_HOSTS` | hostnames/IPs the page answers to |

## C. Run (exactly ONE process)

```bash
tmux new -s webctl -d '.venv/bin/python backend/manage.py runserver 0.0.0.0:8000'
# production-ish single process (never -w >1, the bridge must be unique):
tmux new -s webctl -d '.venv/bin/gunicorn webctl.wsgi:application -b 0.0.0.0:8000 -w 1 --threads 8 -t 120 --chdir backend'
```

Open `http://<control-computer>:8000` → WASD/arrows (hold = drive, release =
stop), Space = e-stop, E = clear e-stop. Status shows bridge connection,
latched cmd, battery, watchdog timeout.

Internet access: put HTTPS in front of 8000 only (Caddy + DDNS like the existing
stack, or Tailscale). Basic Auth is minimum; keep 9090/8080 LAN-only.

## D. Verify without the robot

```bash
.venv/bin/python -u tools/integration_test.py
```

Starts fake rosbridge (:9091) + fake MJPEG cam (:9093) + Django (:8000),
asserts: connect+telemetry, clamping, 10 Hz publish, watchdog stop, e-stop
override, camera proxy, SPA serving, and the single-bridge-owner guard.
Exits and cleans up — safe to re-run.

## Troubleshooting

- `/api/cmd` → 503 "does not own the robot bridge": more than one server
  process started; kill extras (the flock guard protects you, but fix the
  supervision).
- `connected: false`: wrong `ROBOT_WS_URL`, robot's rosbridge down, or
  ROS_DOMAIN_ID mismatch on the robot.
- camera 502: web_video_server down or `CAMERA_TOPIC` guess wrong — force it
  in `.env` after `ros2 topic list`.
- robot doesn't move though page says moving: wheeltec bringup node not
  running on SBC (the bridge only publishes `/cmd_vel`; the bringup node
  forwards it over UART to the STM32).
