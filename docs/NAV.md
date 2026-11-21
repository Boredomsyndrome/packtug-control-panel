# v3.3 — Autonomous navigation (vendor Nav2 pipeline) + waypoints

**Mission:** LiDAR-mapping of a room, mark named points by driving the robot
there (teleop) + "save point", then drive point→point on a server instruction.
Autonomy runs the **vendor wheeltec_ros2 workspace** (slam_toolbox + Nav2
`param_four_wheel_diff_bs.yaml` + `/follow_waypoints`) — nothing in the vendor
ws is modified; our repo only launches it and shuttles state.

**Robot never moves on its own unless a `route` is running; teleop is
unchanged in off/mapping/localizing modes.**

## Architecture (extends v3 pull model — robot stays a pure client)

```
Browser ──POST /api/robot/<id>/nav {action,…}──▶ RobotHub nav mailbox
client.py navd ◀──same ws cmd frame now carries "nav":{seq,action,…}── pump
client.py ──telemetry "nav":{mode,pose,map,route_done…}──▶ hub ──▶ status.nav
client.py ──bash ~/robotctl/navctl.sh <cmd>──▶ vendor launches (slam_toolbox,
             wheeltec_nav2 bringup_launch.py slam:=false autostart:=true)
client.py ──POST /robotctl/<id>/map (base64 pgm+yaml)──▶ .data/maps/<rid>/
Waypoints: JSON file .data/waypoints.json (thread-safe atomic replace; no DB)
```

**Safety invariants extended (never weaken the existing ones):**
* `route` mode = Nav2 owns `/cmd_vel`; the client's 10 Hz teleop publisher is
  SUSPENDED for the duration (mode gate in publish_loop — this was THE
  blocker: both publishers would fight at 10 Hz).
* Any of: latched estop, onboard watchdog trip, `nav_cancel`, `nav_off`,
  SIGTERM ⇒ `NavDaemon.halt()`: cancel `/follow_waypoints` goal +
  `navctl.sh stop` (kills component_container + launcher + slam) + zero.
* Hub refuses `set_cmd` while robot reports nav mode `route` (409) — robot
  side gate is the enforcement; hub check is UX only.
* Nav2 speed ceiling lives in the vendor param (0.3 m/s / 0.5 rad/s) — below
  hub MAX_LIN/MAX_ROT. No clamp bypass: velocity never flows through the hub.
* Feature is opt-in: `WEBCTL_NAV=1` in robotctl.env; when off, client.py
  behaviour is byte-identical to v3.2.

## Robot-side files (repo → deploy)
* `robotctl/navd.py` — mode machine: off|mapping|localizing|route; TF pose
  lookups (`map→base_footprint`, fallback `odom_combined→base_footprint`
  flagged `frame`); ActionClient `/follow_waypoints`; `/initialpose` align;
  map upload after save. Dry-run complete (fake poses) for the harness.
* `deploy/navctl.sh` — subcommands `map-on | map-save <name> | stack-on
  <mapname> [align_wp] | stop | status`. Sources ROS **without** `set -eu`
  (pitfall doc). `stop` kills `component_container_isolated` +
  `bringup_launch.py` + `async_slam_toolbox_node` (TERM, then -9).

**Why not `wheeltec_nav2.launch.py`:** it relaunches base+lidar (port fights
the running stack_up units — lidar driver heap-aborts). We use its
`bringup_launch.py` + `wheeltec_slam_toolbox` params directly instead.
`online_async_launch.py` likewise re-includes base — run
`ros2 run slam_toolbox async_slam_toolbox_node -p params_file:=<vendor yaml>`.

## Wire protocol
server→robot (inside existing cmd snapshot, both ws + http-fallback):
```json
"nav": {"seq": 7, "action": "map_on|map_save|map_off|nav_on|route|nav_cancel|nav_off|record|align",
        "name": "room1", "map": "room1", "poses": [[x, y, yaw], …], "point": "dock"}
```
Client dedupes on `nav.seq`. robot→server telemetry gains:
```json
"nav": {"mode": "…", "map": "room1", "pose": [x, y, yaw]|null, "frame": "map|odom_combined",
        "route_total": 3, "route_done": 1, "error": null,
        "record": {"rid": 4, "name": "dock", "x": 1.2, "y": 0.3, "yaw": 0.9, "frame": "map"}}
```
Waypoint record flow: browser `POST /nav {action:record,name}` → hub stores
pending name + rid → robot TF-lookups pose → telemetry `record` → hub writes
waypoint store (dedupe by rid).

## Browser API
```
GET  /api/robot/<id>/nav          → {state, waypoints:{}, maps:[]}
POST /api/robot/<id>/nav          → see action table (below)
GET  /api/robot/<id>/maps/<name>.<pgm|yaml>
```
| action | body | effect |
|---|---|---|
| map_on / map_off / map_save | name | slam_toolbox vendor bring-up / save pgm+yaml / stop |
| nav_on | map (+align?) | vendor Nav2 bringup (AMCL+costmaps+waypoint_follower) |
| record | name | store current pose as waypoint |
| route | waypoints:[names] | `/follow_waypoints` goal — **the only motion** |
| align | point | `/initialpose` from waypoint (AMCL kick) |
| nav_cancel / nav_off | — | cancel goal / cancel+stop stack |
| delete_map / delete_wp | name | forget files (off-mode only) |

## SPA ("Autonomy" panel)
Buttons: Start mapping · Save map · Stop mapping · Start nav (map picker +
optional align-to-point) · **Save point** (name) · waypoint checklist (order =
click order) · ▶ START ROUTE · cancel · nav off. Map canvas (PGM + pose
arrow + waypoint dots) refreshes 0.5 Hz; nav state rides the existing 1 Hz
fleet poll (`status.nav`), waypoints list via /nav every 2 s.

## Verify
gates 1→2→3 as always; harness additions: shims fake tf2/ActionClient
(navd transitions, route publish gate, estop→halt, dry record); integration
section [10] drives full fake lifecycle through the real ASGI incl. map
upload + waypoint persistence + route 409-on-teleop. Robot live check is on
user order only (motion requires explicit `route`).
