# WHEELTEC S2000 Robot — Setup Procedures (fleet, server-LAN mode)

Target: S200/S2000 brushless kit with onboard SBC (Jetson / Raspberry Pi /
LubanCat) running the WHEELTEC ROS2 (Ubuntu 22.04 / Humble) image.

**v3 architecture: the robot is a PURE CLIENT with ZERO INSTALLS.** The
robot has no internet access and nothing gets installed on it: `client.py`
runs on the Python 3 standard library alone (its WebSocket is hand-rolled
over `socket`); `rclpy` / `cv2` are used from the WHEELTEC image as-is.
The robot turns its factory hotspot OFF, joins the WiFi the control laptop
lives on, and only dials OUT to the Django server (WebSocket primary, HTTP
long-poll automatic fallback). No rosbridge, no listening ports, no
per-robot IP juggling — and the fleet scales by adding registry entries.

```
 [S2000 chassis]──UART──[SBC: ROS2 + robotctl client]
                            │ WiFi STA (client), outbound HTTP only
        ════════════════════╪═══════════════════════════
 [robot #2 client] ──────── ╪ ──── [laptop: Django server :8000] ── browser
                            └────────── (same WiFi / laptop hotspot)
```

## 0. What to bring

- Robots (batteries charged!), 24 V charger(s)
- Control laptop running the Django server (README.md section A)
- The WiFi both use: a home/school router LAN, **or** the laptop's own
  WiFi hotspot with no internet (recommended for school demos — nothing else
  touches it; GNOME hotspots use subnet 10.42.0.0/24, laptop = 10.42.0.1)
- Monitor + keyboard (only to fix an SBC directly)

## 1. Power-on safety check

1. Confirm physical **e-stop** pressed; wheels off the ground / clear area ≥2 m.
2. Battery pack connected; healthy 24 V LiFePO4 ≈ 25–29 V; below ~24 V → charge.
3. Release e-stop only right before driving.

## 2. Boot the robot (hotspot will be turned OFF in step 3)

Flip the power switch; wait ~60–90 s for the SBC. On first setup the factory
hotspot is still on (SSID `WHEELTEC_CAR_…`, password per kit manual, commonly
`dongguan`) — join it from the laptop **one last time** to run step 3.
(If the SBC already joined your WiFi in a previous run, `ssh wheeltec@<its-new-IP>`
instead, or check your router's client list / mDNS `wheeltec.local`.)

```bash
ssh wheeltec@192.168.0.100      # factory AP mode, last time
```

## 3. Hotspot OFF, join the server WiFi (once, persists across reboots)

```bash
# copy the repo first if the robot has no copy yet:
#   scp -r wheeltec-webctl wheeltec@192.168.0.100:~/

cd ~/wheeltec-webctl
sudo bash scripts/robot_wifi_join.sh <YOUR_SSID> '<PSK>' [wlan0]
```

The script: connects as a station, marks that profile autoconnect with top
priority, and disables every AP-mode NetworkManager profile (deleted never —
revert with `nmcli connection modify "<AP name>" connection.autoconnect yes`).
It prints the robot's new IP at the end. Verify from the laptop:

```bash
ping <robot-ip>          # reachable both ways now (robot = client on your LAN)
```

> **No router at the demo?** Turn on the laptop's WiFi hotspot instead. The
> robots join it like any WiFi; the server address is then always
> `http://10.42.0.1:8000` (GNOME hotspot) — nothing needs internet.
> **On a router LAN:** give the laptop a DHCP reservation so
> `WEBCTL_SERVER` never changes for any robot.

## 4. Check the ROS2 base driver (MCU bridge)

```bash
source /opt/ros/humble/setup.bash
source ~/wheeltec_robot/install/setup.bash    # if this dir exists (most kits)
ros2 pkg list | grep turn_on_wheeltec_robot   # confirm the base-driver package
ros2 launch turn_on_wheeltec_robot turn_on_wheeltec_robot.launch.py
# second terminal:
ros2 topic list                          # /cmd_vel /PowerVoltage /odom ...
ros2 topic echo /PowerVoltage --once     # battery voltage → MCU link OK
```

Autostart it on boot with `wheeltec-ros.service` (§6 style, ExecStart =
the source + launch line above) — the robot client unit waits for it.

## 5. Run the robot client (zero installs — the robot has no internet)

The client is ONE python file needing only the stdlib; the repo folder is
all that gets copied (step 3). Config lives in the user's home, nothing
system-wide is required:

```bash
mkdir -p ~/.config
cp ~/wheeltec-webctl/robotctl/robotctl.env.example ~/.config/wheeltec-robotctl.env
chmod 600 ~/.config/wheeltec-robotctl.env
nano ~/.config/wheeltec-robotctl.env       # SERVER + this robot's ID/TOKEN
tmux new -s robotctl 'bash ~/wheeltec-webctl/robotctl/run_client.sh'
```

Within ~1 s you should see a whole boot story (every step logs BEFORE it
runs, so a stall names itself):

```
robotctl: using /home/wheeltec/.config/wheeltec-robotctl.env
robotctl 3.0.0-stdlib starting (python 3.8.x, id=s2000-1)
... INFO robotctl: robotctl 3.0.0-stdlib client 's2000-1' -> http://... (transport=auto ...)
... INFO robotctl: initialising velocity sink (dry_run=False; first rclpy start ...
... INFO robotctl: rclpy: initialising node 'webctl_robot_client' (DDS discovery ...
... INFO robotctl: websocket up -> ws://192.168.50.50:8000/ws/robotctl/s2000-1
... INFO robotctl: camera: resolving source (mode=auto)... (then ros/cv2 attempts)
```

Stuck at a line? It says what to do: last ROS line = base driver/DDS not
up yet; last cv2 line = camera probe slow (that device isn't plug-and-play
on Jetson) → `WEBCTL_CAM_MODE=ros` (with the camera launch running) or
`=off`; chip-side details in §9. `WEBCTL_LOG_LEVEL=DEBUG` for wire chatter.

`~/.config/wheeltec-robotctl.env` per robot: `WEBCTL_ROBOT_ID` +
`WEBCTL_ROBOT_TOKEN` must match one `WEBCTL_ROBOTS` entry on the server,
and `WEBCTL_SERVER` = `http://<laptop-ip-on-that-wifi>:8000`
(hotspot mode → `http://10.42.0.1:8000`, router mode → its DHCP reservation).

**Autostart without installing anything** (uses the touchscreen desktop):
create `~/.config/autostart/wheeltec-robotctl.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=wheeltec robotctl
Exec=bash -lc 'exec bash ~/wheeltec-webctl/robotctl/run_client.sh >> /tmp/robotctl.log 2>&1'
X-GNOME-Autostart-enabled=true
```

(Prefer systemd? An OPTIONAL unit with an env-file is in
`robotctl/wheeltec-robotctl.service` — still zero packages, but it does
write to /etc. The base driver §4 should autostart similarly — the client
retries forever, order doesn't matter.)

**No rosbridge. No ROS ports exposed. Nothing installed. Nothing to
port-forward or firewall.** The transport auto-negotiates: WebSocket when
the server speaks ASGI (`scripts/run_server.sh`), HTTP long-poll otherwise
(`/api` and `/robotctl` are identical either way).

## 6. Camera (optional, per robot — and all sources come from the image)

`WEBCTL_CAM_MODE=auto` tries, in order, and never blocks driving:

1. **ros** — subscribe to the kit's own `sensor_msgs/Image` topic (the
   WHEELTEC camera API: `ros2 launch turn_on_wheeltec_robot
   wheeltec_camera.launch.py`, then `ros2 topic list | grep image`),
   JPEG-encoded with the image's cv2/numpy. Set `WEBCTL_CAM_ROS_TOPIC` if
   the topic isn't one of the usual four.
2. **cv2** — direct `cv2.VideoCapture(WEBCTL_CAM_DEVICE)` for a USB cam at
   `/dev/video0`; for a CSI cam pass its gstreamer
   (`nvarguscamerasrc ! …`) pipeline string as `WEBCTL_CAM_DEVICE`.
3. **mjpeg** — only if `web_video_server` **already ships** in this image
   (`ros2 pkg list | grep web_video_server`; we never apt-install it) —
   point `WEBCTL_CAM_URL` at `http://127.0.0.1:8080/stream`.

Frames go up the same robot websocket as telemetry (or the HTTP frame
endpoint in fallback mode) at `WEBCTL_CAM_FPS`/`WEBCTL_CAM_WIDTH`/
`WEBCTL_CAM_JPEG_Q`. No camera at all is a fully supported state: driving,
telemetry and safety are identical; the UI just shows the camera card.

## 6b. LiDAR (v3.1 — this unit has an **LSLidar M10P**, UART)

Start the vendor driver (it is NOT started by the base launch by default):

```bash
ros2 launch turn_on_wheeltec_robot wheeltec_lidar.launch.py   # = Lsm10_m10p_uart, /dev/wheeltec_lidar
ros2 topic echo /scan --once            # sanity: one ring on the graph
```

The client picks `/scan` up automatically (and discovers LaserScan topics
under ANY name after ~6 s of silence — same self-heal as the camera).
It downsamples to 180 rays and pushes ≤4 small JSON scans/s over the same
socket (frames AND scans ride the dedicated latest-wins uploader, so they
can never starve the keepalive — the 3.0.2 lesson). Tune with
`WEBCTL_SCAN_HZ/_RAYS/_MAX_M`, or `WEBCTL_SCAN_MODE=off`. The browser shows
a live polar plot with a "why is it dead" hint assembled from the robot's
own graph snapshot — if it says *"no LaserScan topic on the robot"*, the
driver above is simply not running; if it says *"topics visible but no
data"*, the driver is running but not publishing (check serial port
permission / `ls -l /dev/wheeltec_lidar`).

## 6c. This exact unit — verified hardware facts (live SSH audit 2026-11-04)

| item | fact |
| --- | --- |
| computer | **Jetson Orin Nano Dev Kit** (L4T R35.3.1, Ubuntu 22.04, Python 3.10, aarch64) — NOT a Pi |
| ROS | `/opt/ros/humble` + vendor ws `~/wheeltec_ros2` (colcon-built; `turn_on_wheeltec_robot`, `lslidar_driver`, `usb_cam-ros2`, `web_video_server-ros2`, nav2, slam…) |
| serials | 3× CH343: `/dev/wheeltec_controller` = MCU base @115200 (launch `base_serial.launch.py`, publishes `/PowerVoltage` `/odom`), `/dev/wheeltec_lidar` = **LSLidar M10P** (±120° FOV), `/dev/wheeltec_mic` |
| tmux | NOT installed — use nohup (`deploy/stack_up.sh` handles all of it; crontab `@reboot` keeps the stack up: base + lidar + webctl client, idempotent). Vendor `lslidar_driver_node` also **crashes at runtime** (observed -6 heap-abort and -11 SIGSEGV while streaming) → `deploy/watchdog_lidar.sh` on `*/2 * * * *` cron revives it; the webctl client self-heals once `/scan` returns |
| camera | **NO working camera.** CSI: no IMX219 module electrically present — driver bind on both CSI ports i2c-probes with `-121` (both `rbpcv2_imx219_a/c` nodes exist but nothing answers; the vendor DT enables them by default). USB: an **Orbbec ASTRA S** (2bc5:0402) is plugged in but exposes only Vendor-Specific/OpenNI interfaces (no UVC → no /dev/video) and the vendor `ros2_astra_camera` pkg does NOT support model S (only DaBai/Gemini). Any UVC webcam plugged in → /dev/video0 → stack_up + client auto-discovery light it up with zero changes. Earlier "CSI works" belief was WRONG — past frames were the client's synthetic test pattern. |
| VPN | robot is the hwdsl2 client: tunnel iface `ppp0` = 192.168.42.11 → server at 192.168.42.1:8090 (`~/robotctl/robotctl.env`) |

## 7. Server + first drive

Server (laptop): see README.md §A. Then on the robot:

```bash
curl -su "$WEBCTL_ROBOT_ID:$WEBCTL_ROBOT_TOKEN" http://<server>:8000/robotctl/<id>/cmd?wait=0
# {"ok": true, "vx": 0.0, ...} proves the robot reaches the server
```

Open `http://<laptop>:8000` in a browser:
1. A chip per robot lights green within ~2 s of its client reaching the
   server (poll cadence 0.4 s).
2. Pick a robot → battery voltage shows → wheels in the air → tap ▲.
3. Release → stops within ~0.5 s (server staleness). Kill WiFi on the robot
   → stops within ~1 s anyway (onboard watchdog) — chip turns red.
4. Space or ■ E-STOP → dead stop, latched; any new cmd or STOP resumes.
5. Any second device on the same WiFi opens the same page: pick another
   robot, drive two at once. Commands never cross robots (per-robot hub +
   per-robot token; the suite asserts it).

## 8. Demo-day checklist

- [ ] All batteries ≥ 25 V; e-stops reachable at the table
- [ ] Each robot: chip green on the page, battery shown, drive 30 s, stop <1 s
- [ ] Laptop = the only machine running the server (flock kills doubles)
- [ ] `integration_test.py` green on the laptop before packing
- [ ] Robot autostart verified (power-cycle robot → chip greens by itself ≤90 s)
- [ ] Laptop hotspot on (or router LAN confirmed), server URL known

## 9. Troubleshooting

- **only `robotctl: using ...` printed, nothing after** → the wrapper died
  before exec-ing python. Fixed in the current `run_client.sh` (it used
  `set -eu` around sourcing ROS setup files — colcon scripts touch unset
  `$COLCON_PREFIX_PATH`, killing the shell with the error hidden). Still
  seeing it? You have an OLD wrapper: `bash run_client.sh; echo $?` →
  instant `1` = that bug. Redeploy the tarball. The current wrapper prints
  `robotctl: exec ...` right before python starts and strips `\r` from
  env files edited on Windows (USB copies!). Quick sanity without wrapper:
  `python3 robotctl/client.py --version` → `robotctl 3.0.0-stdlib`
  (bare client.py *without* the env sourced rightly exits
  `WEBCTL_ROBOT_ID is required` — that error is normal there, use the wrapper).
- **chip red / robot OFFLINE** → the client log (`tmux attach -t robotctl`
  or `/tmp/robotctl.log` with autostart): `Connection refused` (server down
  / wrong IP), `401`/handshake refused (token mismatch vs `WEBCTL_ROBOTS`),
  DNS issues (use a plain IP + http on LAN). The log says which transport
  is live; `ws down ... — using HTTP fallback` means the server has no ASGI
  running (still fully drivable, just higher video latency).
- **robot joined but no IP** → router DHCP full / captive portal: school WiFi
  with captive portals is NOT supported (as before); laptop hotspot is.
- **hotspot came back after reboot** → an AP profile still has
  `connection.autoconnect yes`: rerun `scripts/robot_wifi_join.sh`.
- **moves then stops after ~1 s while holding a button** → your browser tab
  isn't focused/visible; heartbeat stopped — intended behavior.
- **camera unavailable, driving fine** → no source resolved per §6; the
  client log lists what it tried (ros topic names / cv2 device / mjpeg).
- **motors dead though UI says driving** → base driver (step 4) not running
  or MCU serial down: `ls -l /dev/wheeltec_controller*`.

## 10. Factory recovery / reverting

v3 robot-side footprint = one copied folder, one env file in `~/.config`,
one autostart entry (optional), one NetworkManager profile change. Revert:

```bash
tmux kill-session -t robotctl                      # stop the client
rm -f ~/.config/autostart/wheeltec-robotctl.desktop ~/.config/wheeltec-robotctl.env
nmcli connection modify "<AP name>" connection.autoconnect yes   # hotspot back
nmcli connection delete "<your wifi profile>"                     # optional
# (only if you used the optional systemd unit:)
sudo systemctl disable --now wheeltec-robotctl 2>/dev/null || true
sudo rm -f /etc/systemd/system/wheeltec-robotctl.service /etc/wheeltec-robotctl.env
```

Nothing was ever installed on the STM32; factory `teleop_twist_keyboard`
still works to confirm the chassis is stock.

## Optional: drive over the internet later (home)

LIVE since 2026-11-03: the server runs on potatoserver; connect the robot
with Tailscale (recommended — `docs/ACCESS.md` option A) and set in
`~/.config/wheeltec-robotctl.env`:

```
WEBCTL_SERVER=http://<potato-tailscale-100.x.y.z>:8090
```

or the HTTPS edge once Caddy :8443 exists (ACCESS.md option B):
`WEBCTL_SERVER=https://comet13579.duckdns.org:8443`. Remember the
`ALLOWED_HOSTS` + server-restart step after any IP change. Robots still
dial out; still zero exposed robot ports. Keep clamps and watchdogs —
and expect 100–300 ms latency on cellular: crawl speed only.
Endpoints and robot-side wire protocol: `docs/API.md`.
