# wheeltec-webctl — API Reference

Live production instance (since 2026-11-03): **potatoserver**, uvicorn ASGI
on `0.0.0.0:8090`, tmux session `webctl`, state in `.server.lock`-owned
process. Secrets in repo `.env` (chmod 600, never committed).

```
URL                                      What
http://127.0.0.1:8090                    localhost
http://192.168.1.142:8090                LAN
https://comet13579.duckdns.org:8443      WAN via Caddy (see SETUP.md §VPN/HTTPS — pending port-forward)
http://<tailscale-ip>:8090               robot's planned VPN path
```

Everything is JSON `{"ok": true|false, ...}` unless stated otherwise.
Two **separate** credential systems that never mix:

| Who | Credential | Sent as |
|---|---|---|
| Browser / operator | `WEBCTL_AUTH_USER/_PASS` | HTTP Basic; ws via `?browser_auth=<b64(user:pass)>` |
| Robot | its `id:token` from `WEBCTL_ROBOTS` | HTTP Basic (username = id); ws via `?token=` |

Robot ids: `[A-Za-z0-9_-]{1,32}`. Empty token = dev-open (never do that in
this `.env`). Unknown id → 404/4404, bad creds → 401/4401, a second server
process → 503/4503 (single-owner flock).

---

## 1. Browser / operator API

All requests need Basic auth. Example fleet read:

```bash
curl -u comet:*** http://127.0.0.1:8090/api/robot
```

### `GET /api/robot`
`{"ok": true, "robots": [<status>...], "camera_path": "/cam/<id>"}`

### `GET /api/robot/<id>` — one robot's live status
| field | meaning |
|---|---|
| `online` | robot contacted the server within ~3 s |
| `battery_v`, `battery_age` | last Float32 volt telemetry + s since |
| `cmd`, `cmd_age` | last accepted command + age (zeroed display if stale > `CMD_TIMEOUT`) |
| `applied` | what the robot reports actually publishing (telemetry) |
| `estop` | e-stop latched? |
| `watchdog_trips` | robot-side watchdog zeroing count (see §safety) |
| `transport`, `ws_connected` | `ws` or `http`, live socket? |
| `seq` | monotonic command sequence counter |

### `POST /api/robot/<id>/cmd` — drive
`{"vx": m/s, "wz": rad/s}` — non-finite or |value| > 1e6 → 400 with
`errors`. Values are **clamped server-side** to `±MAX_LIN` (0.5) /
`±MAX_ROT` (0.7); response echoes what was actually stored plus
`"clamped": true|false`. A fresh cmd also **unlatches estop**.

```bash
curl -u comet:*** -H 'Content-Type: application/json' \
     -d '{"vx":0.25,"wz":0.1}' http://127.0.0.1:8090/api/robot/s2000-1/cmd
```

### `POST /api/robot/<id>/stop`
Zero velocity **and** clear a latched e-stop. No body.

### `POST /api/robot/<id>/estop`
Latch zero immediately; held until the next explicit `cmd`. Robot-side
receives `estop` frames even mid-stream.

### Camera (two renderings of the same hub frame buffer)
* `GET /cam/<id>` — legacy MJPEG `multipart/x-mixed-replace` stream
  (handy with `mpv`/browser tab).
* `ws://…/ws/cam/<id>?browser_auth=<b64>` — live viewer socket the React
  SPA uses. Each binary message = `struct '<d'` server-send-timestamp (s)
  + JPEG bytes; client subtracts its clock for latency display.

Frames are latest-wins and capped at `MAX_FRAME` (2 MiB) server-side;
`camera: true` in status means a frame arrived within ~5 s.

### Validation rules (400 shapes)
`VelocityCommandSerializer` rejects missing/non-numeric/NaN/Inf/±1e6-range
fields: response `{"ok": false, "error": "invalid command", "errors": {...}}`.

---

## 2. Robot-facing API (what `robotctl/client.py` speaks)

Robots make **outbound calls only** (no listening ports, ever).

### WebSocket primary — `ws://<server>/ws/robotctl/<id>?token=<token>`
1. handshake: Basic header **or** `?token=` (and `?u=` if username ≠ id)
2. optional first client text frame:
   `{"type":"hello","id":…,"cam":true|false,"sw":"3.0.2-stdlib"}`
3. server → robot (text):
   * `{"type":"welcome","seq":N}` on accept
   * `{"type":"cmd","vx":…,"wz":…,"seq":N,"estop":false}` pushed on every
     change **and re-checked for staleness at push time** (never stale)
   * `{"type":"takeover"}` — a second connection for the same id won; idle
     5 s before reconnecting (browser reconnect beats robot hiccup)
4. robot → server (text, ~1 Hz):
   `{"type":"telemetry","battery_v":24.2,"applied_vx":0.0,"applied_wz":0.0,
     "watchdog_trips":0,"transport":"ws"}`
5. robot → server (binary): latest camera JPEG only (uploader drops stale
   frames; one in flight)
6. keepalive: robot sends ws **ping** every min(0.8 s, WATCHDOG/3); uvicorn
   auto-pongs; any pong counts as contact for the onboard watchdog.

Close codes: `4401` bad token, `4404` unknown robot, `4503` server is not
the lock owner, `1013` hub lost mid-session.

### HTTP long-poll fallback (automatic when ws unreachable)
* `GET /robotctl/<id>/cmd?wait=0.4[&seq=N]` → 200
  `{"vx":…,"wz":…,"seq":…,"estop":…}` when there is something fresh,
  204 while nothing changed. Returns **effective** velocity: forced zero
  while estop latched or `cmd` older than `CMD_TIMEOUT` (checked at serve
  time). 401/404/503 mirror the ws close codes.
* `POST /robotctl/<id>/telemetry` — same JSON body as above
* `POST /robotctl/<id>/frame` — body = raw `image/jpeg`

### Robot onboard safety (independent of all of the above)
No command AND no ws pong for `WEBCTL_WATCHDOG` s (default 2.5) → client
publishes zero directly, keeps counting `watchdog_trips`, and shows up
`state:"watchdog"` in telemetry. SIGTERM also zeroes. First-ever boot while
parked does **not** count as a trip.

---

## 3. Robot-side ROS 2 surface (what the client touches on the robot)

| direction | interface | type | notes |
|---|---|---|---|
| publishes | `/cmd_vel` (env `WEBCTL_CMD_VEL_TOPIC`) | `geometry_msgs/msg/Twist` | 10 Hz while driving; linear.x + angular.z; QoS depth-1 reliable/volatile |
| subscribes | `/PowerVoltage` (+ `WEBCTL_BATTERY_TOPICS`) | `std_msgs/msg/Float32` | volt telemetry |
| subscribes | `/camera/image_raw` (+ `WEBCTL_CAM_ROS_TOPICS`) | `sensor_msgs/msg/Image` | sensor-data QoS (best-effort); accepts rgb8/bgr8/rgba8/bgra8/mono8, packed yuyv422/uyvy/yuv422, planar nv12/nv21/i420; padded rows via `msg.step` |

Nothing else on the ROS graph is read or written; launching/stopping the
vendor base & camera nodes stays your job (`ros2 launch wheeltec_robot …`).

### Verify the base is consuming commands
```bash
ros2 topic info /cmd_vel -v      # Subscription count >= 1 = base driver live
ros2 topic echo /cmd_vel --once  # nonzero Twist while pushing the web stick
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.1}}" -r 10   # bypasses webctl entirely
```

## 4. External reading (vendor + ROS)

* WHEELTEC ROS 2 source of truth: https://github.com/huang-kai/wheeltec_robot_ros2
  (base driver, camera launch, `PowerVoltage` publisher live here)
* DeepWiki walkthrough of that repo (searchable):
  https://deepwiki.com/huang-kai/wheeltec_robot_ros2
* English hands-on guide for WHEELTEC bases (topics, frames, foxglove):
  https://jkk-research.github.io/workshops/wheeltec_real_b/
* ROS 2 Humble msg reference: https://docs.ros.org/en/humble/p/geometry_msgs/
  (`Twist` layout) and `ros2 topic` CLI: https://docs.ros.org/en/humble/Get-Started.html

## 5. Ops quick reference (potatoserver)

```bash
tmux attach -t webctl                                   # watch server log
tmux new-session -d -s webctl "cd ~/wheeltec-webctl && \
  bash scripts/run_server.sh 0.0.0.0 8090 2>&1 | tee -a .logs/server-8090.log"
bash tools/e2e_prod_check.sh    # loopback dry-robot drives through the LIVE instance
curl -u comet:*** http://127.0.0.1:8090/api/robot     # fleet
```

Register another robot: append `,<id>:<token>` to `WEBCTL_ROBOTS` in `.env`,
restart the tmux session, point a client at it — the fleet is data, not code.
