# wheeltec-webctl — WHEELTEC S2000 fleet browser control (pull-mode v3)

Control **multiple** WHEELTEC brushless robots (ROS2 SBC on board) from one
web page. **The Django server on your laptop is the ONLY server in the
system**: robots are pure outbound-only clients (WebSocket primary, HTTP
long-poll fallback) — no rosbridge, no listening ports, no hotspot on the
robots — and **nothing is ever installed on a robot** (the client is pure
Python stdlib; the robot may be permanently offline). Designed for one
robot, scales by registry entries.

> **Setting up? Start at [`SETUP.md`](SETUP.md)** — ordered zero→fleet-driving
> guide. Bringing a router to school? [`SCHOOL_SETUP.md`](SCHOOL_SETUP.md) —
> offline-LAN plan (WAN stays empty, one DHCP reservation, zero school deps).
> `ROBOT_SETUP.md` = exhaustive robot-side reference; this README =
> architecture, API and verification.

```
 browser (any device on the LAN)                LAPTOP = the only server
   │ no password, WASD/hold-to-move            ┌───────────────────────────┐
   │ POST /api/robot/<id>/cmd   (10 Hz)        │ Django :8000              │
   ├──────────────────────────────────────────▶│  per-robot hub:           │
   │ GET /api/robot  (fleet status, 1 Hz)      │   • clamp MAX_LIN/MAX_ROT │
   │ GET /cam/<id>   (MJPEG relay)             │   • staleness watchdog    │
   ◀───────────────────────────────────────────┤   • e-stop latch          │
                                               │  camera frame relay       │
 robots — PURE CLIENTS, outbound only          │  flock: one process owns  │
   robotctl/client.py (Python-STDLIB-ONLY)     └───────────▲───────────────┘
   • ws  /ws/robotctl/<id>  ◀── cmd push ──────┬───────────┘ one ws/robot
   • same ws: telemetry JSON + JPEG frames ────┘  (HTTP long-poll fallback)
        │
        ▼ ROS2 (localhost only): /cmd_vel → STM32 → hub motors
```

## Why pull-mode (vs. v1 rosbridge push)

| | v1 (rosbridge WS) | v2 (this) |
|---|---|---|
| robot listens on | 9090 + 8080 + 22 | **nothing** |
| network topology | robot AP ⇒ one robot visible | robot joins shared WiFi ⇒ **whole fleet** |
| server must reach robot IP | yes (fragile DHCP/AP) | never — robot dials home |
| multiple robots | one bridge = one robot | N registry entries |
| command transport | server pushes WS | robot ws (HTTP long-poll fallback) |

Safety is now **two-layered**: the server never serves a command older than
`CMD_TIMEOUT` (0.5 s) or during a latched e-stop, AND each robot client has
an onboard watchdog that publishes zero if the server itself becomes
unreachable for `WEBCTL_WATCHDOG` (1.0 s) — verified in the integration
suite by SIGSTOPping the server under load.

## A. Server (laptop) — once

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cd frontend && npm ci && npm run build && cd ..
cp .env.example .env      # register the fleet + set passwords (see comments)
.venv/bin/python -u tools/integration_test.py    # ALL CHECKS PASSED? proceed
```

Run (exactly ONE process — the flock refuses second ones for a reason):

```bash
tmux new -s webctl -d 'bash scripts/run_server.sh 0.0.0.0 8000'   # uvicorn ASGI
# production smoke (curl battery + real ws handshake):
bash tools/prod_smoke_test.sh
```
`runserver`/`gunicorn webctl.wsgi` still boot an HTTP-only mode (robots then
auto-fall back to long-poll, camera relay to /cam MJPEG) — but run ONE
process; the ASGI app is what serves WebSockets.

`.env` keys: `WEBCTL_ROBOTS="alpha:tokA,beta:tokB"` (generate a random token
per robot) and `MAX_LIN/MAX_ROT/CMD_TIMEOUT`. Since v3.2 there is **no
browser password** — access control is the network (VPN / trusted LAN);
per-robot tokens are the only credentials in the system.

Network for the LAN: any WiFi both laptop and robots join works. Simplest
without router: turn ON the laptop's WiFi hotspot — GNOME uses 10.42.0.0/24,
the laptop is **10.42.0.1**, robots join it and call the server there. On a
router LAN, reserve the laptop's address in the router (robots need a stable
`WEBCTL_SERVER`).

## B. Each robot — once (full procedure: ROBOT_SETUP.md)

```bash
# 1. hotspot off, join the server WiFi (persistent; nmcli config, no installs)
sudo scripts/robot_wifi_join.sh <SSID> <PSK>
# 2. copy repo, run the client — ZERO INSTALLS on the robot (no internet!)
mkdir -p ~/.config && cp robotctl/robotctl.env.example ~/.config/wheeltec-robotctl.env
chmod 600 ~/.config/wheeltec-robotctl.env && nano ~/.config/wheeltec-robotctl.env
tmux new -s robotctl 'bash ~/wheeltec-webctl/robotctl/run_client.sh'
```

`client.py` runs on the **Python 3 standard library alone** (its WebSocket is
implemented over raw sockets — nothing to pip install on the offline robot);
`rclpy` and `cv2` come from the WHEELTEC image itself. The robot needs only:
the ROS2 base driver (`turn_on_wheeltec_robot`), this client, and NOTHING
else. No rosbridge, no ports open, nothing to firewall. (An OPTIONAL
systemd unit for autostart ships in `robotctl/`.)

## API reference

Browser (no auth — v3.2; the network is the trust boundary):

| route | meaning |
|---|---|
| `GET  /api/robot` | fleet: every robot's status |
| `GET  /api/robot/<id>` | one robot's status (battery, cmd, estop, camera) |
| `POST /api/robot/<id>/cmd` `{"vx","wz"}` | hold-to-move; validated + clamped |
| `POST /api/robot/<id>/stop` | zero velocity AND clear latched e-stop |
| `POST /api/robot/<id>/estop` | latch zero until next explicit cmd |
| `GET  /cam/<id>` | MJPEG relay of the frames that robot pushes up |

Robot (Basic `<id>:<token>`; per-robot registry token — the only credential):

Primary (WebSocket, robot dials out; `?token=` = that robot's registry token):

| route | meaning |
|---|---|
| `ws /ws/robotctl/<id>?token=…` | server→robot: `{type: welcome\|cmd, vx, wz, estop, seq}` · robot→server: `telemetry` JSON + binary JPEG frames |
| `ws /ws/cam/<id>` | browser viewer: binary = float64 server ts + JPEG, drop-latest |

Fallback (HTTP — used automatically when the server has no WS, e.g. the
gunicorn/WSGI mode; Basic `<id>:<token>` as above):

| route | meaning |
|---|---|
| `GET  /robotctl/<id>/cmd?seq=N&wait=0.4` | long-poll the effective cmd `{vx,wz,estop,seq,cmd_timeout}` |
| `POST /robotctl/<id>/telemetry` | `{battery_v, applied_vx, applied_wz, watchdog_trips}` |
| `POST /robotctl/<id>/frame` | one JPEG per request (≤ MAX_FRAME) |
| `POST /robotctl/<id>/scan` | one packed LaserScan JSON (≤4 Hz, ≤64 KB) |
| `GET  /api/robot/<id>/scan` | newest LiDAR ring + freshness age (SPA polls it) |

## C. Verify without any robot (always after changes)

```bash
.venv/bin/python -u tools/integration_test.py     # 48 checks
```

Spawns fake MJPEG cam (:9103) + uvicorn ASGI (:8100) + duplicate (:8111) +
**two real robot clients in dry-run — one on WebSocket, one forced onto the
HTTP fallback** (sink files record exactly what a real base would receive),
plus a raw ws protocol probe. Asserts: fleet listing/online/offline, both
transports + telemetry, ws push latency <150 ms, staleness auto-zero PUSHED,
ws takeover, camera frames over ws→viewer + legacy /cam relay, 10 Hz
publishing, per-robot isolation, token auth on http AND ws, clamp +
serializer 400s, e-stop latch + unlatch, no spurious watchdog trips on an
idle link, **client watchdog on a frozen (SIGSTOP'd) server** (both
transports reconnect), SPA, flock single-owner 503 (http + ws).
Ports are 8100/8111/9103 — this host has live services on 8000/8001/8080;
never bind/kill those.

## Safety model (brushless — non-negotiable)

- velocity clamped server-side before a robot can ever see it (`MAX_LIN`, `MAX_ROT`)
- browser heartbeat ~10 Hz; held command older than `CMD_TIMEOUT` → zero served
- e-stop latches zero; `/stop` or an explicit new cmd unlatches
- robot onboard watchdog: no server contact (cmd or keep-alive pong) for
  `WEBCTL_WATCHDOG` s → zero
- one server process owns all robot state (flock `.server.lock`); others 503
- robots expose no ports at all; only the Django port needs network access
- physical e-stop stays reachable; test with wheels in the air first

## Troubleshooting

- **robot OFFLINE in UI** → client down or WiFi joined wrong: client log
  (`tmux attach -t robotctl`, or `/tmp/robotctl.log` with autostart); test
  reachability from the robot:
  `curl -u <id>:<token> http://<server>:8000/robotctl/<id>/cmd?wait=0`.
- **401** on polls → token mismatch with `WEBCTL_ROBOTS` entry.
- **503 "does not own"** → two server processes running; keep one.
- **robot moves then stops ~1 s later** → browser tab lost focus / heartbeat
  died (by design — that's the watchdog working).
- **camera "unavailable"** → no camera source resolved on that robot
  (`WEBCTL_CAM_MODE`; check the client log for which of ros/cv2/mjpeg was
  tried); driving is unaffected by camera failure by design.
- **UI shows transport `http` though the server has ws** → robot client is
  an old stdlib-less build, or the ws handshake is being refused (token).
- **motors dead though UI says driving** → base driver not running on the
  robot (client only publishes `/cmd_vel`; `turn_on_wheeltec_robot` forwards
  it to the STM32).
