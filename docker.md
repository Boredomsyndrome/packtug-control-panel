# Docker — packtug webctl hub (fresh server deployment)

What this is: the fused FastAPI hub (robot plane + arm/vision bridge + combined
SPA, one process on `:8090`) packaged as a Docker image and deployed on the
**server** via docker compose.

The **robot side is never containerized** — `robot/client.py` runs natively on
the robot (it needs the ROS/DDS environment and device access; the image doesn't
include the client at all).

## Layout

| File | Role |
|---|---|
| `Dockerfile` | builds the image (`python:3.14-slim` + backend + server + committed `dist/`) |
| `.dockerignore` | keeps the build context tiny |
| `docker-compose.yml` | the deployment entrypoint — build + run + volume + env |

## 1. Prerequisites (server)

- Docker Engine + Docker Compose v2 (`docker compose version`)
- This repo checked out on the server at the tag/branch you want to deploy
- `dist/` is committed, so **no Node build is needed** for a stock deploy.
  Only when the frontend changes: `cd agv-console && npm ci && npm run build`

## 2. Configure `.env.server`

Create `.env.server` next to `docker-compose.yml` (compose loads it via
`env_file`). It holds the fleet registry — one entry per robot, `id:token`:

```bash
WEBCTL_ROBOTS="robot-1:GENERATED_TOKEN,robot-2:GENERATED_TOKEN"
MAX_LIN=0.5
MAX_ROT=0.7
CMD_TIMEOUT=0.5
```

Generate a token per robot, e.g. `openssl rand -hex 16`. Keep the file
**chmod 600 and gitignored** — never commit tokens. Robot ids must match
`[A-Za-z0-9_-]{1,32}`.

If `WEBCTL_ROBOTS` is unset it falls back to a single dev default with an empty
token (auth off) — fine for a throwaway test, never for a real fleet.

## 3. Build & run

```bash
docker compose up -d --build
```

That builds the image (`packtug-webctl:v4.0.0`, ~619 MB, local-only — do not
push), creates the `webctl-data` volume, and starts the service with
`restart: unless-stopped`. Everyday commands:

```bash
docker compose ps              # status + health
docker compose logs -f webctl  # logs
docker compose down            # stop (volume + data survive)
```

## 4. Verify

```bash
curl -s http://127.0.0.1:8090/api/health
# {"status":"ok",...}

docker inspect --format '{{.State.Health.Status}}' webctl
# healthy        (the image HEALTHCHECK probes /api/health)
```

Then open `http://<server-lan-ip>:8090/` (hub index), `/agv/` (robot console),
`/panel/` (arm/vision panel) from a LAN machine.

## 5. Environment variables

| Var | Default | Meaning |
|---|---|---|
| `WEBCTL_ROBOTS` | dev default, auth off | Fleet registry `id:token,id2:token2`. The token is what the robot sends as HTTP Basic / `?token=` on the websocket. |
| `WEBCTL_DATA_DIR` | `/data` in the image | Waypoints + nav maps. Keep it on the volume or they die with the container. |
| `WEBCTL_LOCK` | `/app/.server.lock` | Single-instance lock; set only for a custom path. |
| `WEBCTL_LOG_LEVEL` | `INFO` | App logging (`robot.api/hub/ws` included). |
| `PACKTUG_MOCK` | **`1` (mock)** | Arm bridge mock. A real Ned1 needs `PACKTUG_MOCK=0` **and** `PACKTUG_ARM_HOST=<ip>` **and** `PACKTUG_ARM_CONNECT=1` — explicit triple opt-in, so a fresh container can never touch real hardware by accident. |
| `PACKTUG_DISABLED` | unset | `1` = robot plane off (SPA + arm bridge only). |
| `PACKTUG_DEBUG` | `true` | Debug mode. |
| `MAX_LIN` / `MAX_ROT` | `0.5` / `0.7` | Velocity caps enforced server-side (m/s, rad/s). |
| `CMD_TIMEOUT` | `0.5` | Stale-command window (s). |
| `MAX_FRAME` | built-in | Frame rate cap. |

Real-arm note: `pyniryo` is **not** in the image (commented in
`backend/requirements.txt`). To drive a physical Ned1 from the container, add a
derived Dockerfile with `pip install pyniryo==1.1.2 --no-deps`.

## 6. Connect a robot (native, not Docker)

```bash
WEBCTL_SERVER=http://<server-lan-ip>:8090 \
WEBCTL_ROBOT_ID=robot-1 WEBCTL_ROBOT_TOKEN=<token from .env.server> \
python3 robot/client.py
```

The v4.0.0 client is **websocket-only**: telemetry/scan/frames/commands all ride
one ws; there is no HTTP fallback while the ws is healthy. Watch the server side:

```bash
docker compose logs -f webctl | grep -E "websocket up|robot.api"
```

## 7. Pitfalls

- **Volume naming:** compose normally prefixes volumes with the project name.
  The compose file pins `name: webctl-data` — do not remove that pin, or you get
  a fresh empty volume and the fleet's waypoints/maps silently "vanish".
- **Networking:** `8090` is published on all interfaces on purpose so LAN robots
  can reach it. Keep it private by NOT forwarding 8090 on the router. For a
  machine-only test, bind `127.0.0.1:8090:8090` instead.
- **One hub per port:** the app takes a single-instance lock; a second hub on
  `:8090` (host-native or container) will refuse to start or steal robots.
- **Image tag:** pinned in `docker-compose.yml`; `up -d` after changing it
  recreates the container and the volume data survives.
