# wheeltec-webctl — WHEELTEC S2000 fleet browser control (pull-mode v2)

Control **multiple** WHEELTEC brushless robots (ROS2 SBC on board) from one
web page. **The Django server on your laptop is the ONLY server in the
system**: robots are pure HTTP clients that call it — no rosbridge, no
websockets, no listening ports, no hotspot on the robots.

```
 browser (any device on the LAN)                LAPTOP = the only server
   │ Basic Auth, WASD/hold-to-move             ┌───────────────────────────┐
   │ POST /api/robot/<id>/cmd   (10 Hz)        │ Django :8000              │
   ├──────────────────────────────────────────▶│  per-robot hub:           │
   │ GET /api/robot  (fleet status, 1 Hz)      │   • clamp MAX_LIN/MAX_ROT │
   │ GET /cam/<id>   (MJPEG relay)             │   • staleness watchdog    │
   ◀───────────────────────────────────────────┤   • e-stop latch          │
                                               │  camera frame relay       │
 robots — PURE CLIENTS, outbound HTTP only     │  flock: one process owns  │
   robotctl/client.py on each robot            └───────────▲───────────────┘
   • GET  /robotctl/<id>/cmd   long-poll  ◀────────────────┘ one conn/robot
   • POST /robotctl/<id>/telemetry   (battery, applied cmd)
   • POST /robotctl/<id>/frame       (camera JPEGs, optional)
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
| command transport | server pushes WS | robot long-polls HTTPS |

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
tmux new -s webctl -d '.venv/bin/python backend/manage.py runserver 0.0.0.0:8000'
# production-ish:
bash tools/prod_smoke_test.sh   # starts gunicorn -w1 --threads 16, curls, kills
```

`.env` keys: `WEBCTL_ROBOTS="alpha:tokA,beta:tokB"` (generate a random token
per robot), `MAX_LIN/MAX_ROT/CMD_TIMEOUT`, browser `WEBCTL_AUTH_USER/PASS`
(required before anything crosses a untrusted network).

Network for the LAN: any WiFi both laptop and robots join works. Simplest
without router: turn ON the laptop's WiFi hotspot — GNOME uses 10.42.0.0/24,
the laptop is **10.42.0.1**, robots join it and call the server there. On a
router LAN, reserve the laptop's address in the router (robots need a stable
`WEBCTL_SERVER`).

## B. Each robot — once (full procedure: ROBOT_SETUP.md)

```bash
# 1. hotspot off, join the server WiFi (persistent, survives reboot)
sudo scripts/robot_wifi_join.sh <SSID> <PSK>
# 2. copy repo, install client autostart
sudo cp robotctl/wheeltec-robotctl.service /etc/systemd/system/
sudo cp robotctl/robotctl.env.example /etc/wheeltec-robotctl.env && sudo nano /etc/wheeltec-robotctl.env
sudo systemctl daemon-reload && sudo systemctl enable --now wheeltec-robotctl
```

The robot needs only: the ROS2 base driver (`turn_on_wheeltec_robot`), this
client, and optionally `web_video_server` **bound to 127.0.0.1** for the
camera. No rosbridge, no ports open, nothing to firewall.

## API reference

Browser (Basic Auth when enabled):

| route | meaning |
|---|---|
| `GET  /api/robot` | fleet: every robot's status |
| `GET  /api/robot/<id>` | one robot's status (battery, cmd, estop, camera) |
| `POST /api/robot/<id>/cmd` `{"vx","wz"}` | hold-to-move; validated + clamped |
| `POST /api/robot/<id>/stop` | zero velocity AND clear latched e-stop |
| `POST /api/robot/<id>/estop` | latch zero until next explicit cmd |
| `GET  /cam/<id>` | MJPEG relay of the frames that robot pushes up |

Robot (Basic `<id>:<token>`; middleware never gates browser creds here):

| route | meaning |
|---|---|
| `GET  /robotctl/<id>/cmd?seq=N&wait=0.4` | long-poll the effective cmd `{vx,wz,estop,seq,cmd_timeout}` |
| `POST /robotctl/<id>/telemetry` | `{battery_v, applied_vx, applied_wz, watchdog_trips}` |
| `POST /robotctl/<id>/frame` | one JPEG per request (≤ MAX_FRAME) |

## C. Verify without any robot (always after changes)

```bash
.venv/bin/python -u tools/integration_test.py
```

Spawns fake MJPEG cam (:9103) + Django (:8100) + duplicate Django (:8111) +
**two real robot clients in dry-run mode** (sink files record exactly what a
real base would receive). Asserts: fleet listing/online/offline, telemetry
uplink, 10 Hz publishing, command routing + per-robot isolation, token auth
(401/404), clamp + serializer 400s, server staleness zero, e-stop latch +
unlatch, **client watchdog on a frozen (SIGSTOP'd) server**, camera
push→relay, SPA, flock single-owner 503. Self-cleaning; safe to re-run.
Ports are 8100/8111/9103 — this host has live services on 8000/8001/8080;
never bind/kill those.

## Safety model (brushless — non-negotiable)

- velocity clamped server-side before a robot can ever see it (`MAX_LIN`, `MAX_ROT`)
- browser heartbeat ~10 Hz; held command older than `CMD_TIMEOUT` → zero served
- e-stop latches zero; `/stop` or an explicit new cmd unlatches
- robot onboard watchdog: no successful poll for `WEBCTL_WATCHDOG` s → zero
- one server process owns all robot state (flock `.server.lock`); others 503
- robots expose no ports at all; only the Django port needs network access
- physical e-stop stays reachable; test with wheels in the air first

## Troubleshooting

- **robot OFFLINE in UI** → client down or WiFi joined wrong:
  `journalctl -u wheeltec-robotctl` on the robot; test reachability from it:
  `curl -u <id>:<token> http://<server>:8000/robotctl/<id>/cmd?wait=0`.
- **401** on polls → token mismatch with `WEBCTL_ROBOTS` entry.
- **503 "does not own"** → two server processes running; keep one.
- **robot moves then stops ~1 s later** → browser tab lost focus / heartbeat
  died (by design — that's the watchdog working).
- **camera "unavailable"** → that robot's `WEBCTL_CAM_URL` unset, or
  web_video_server down (`journalctl -u wheeltec-camera`); driving is
  unaffected by camera failure by design.
- **motors dead though UI says driving** → base driver not running on the
  robot (client only publishes `/cmd_vel`; `turn_on_wheeltec_robot` forwards
  it to the STM32).
