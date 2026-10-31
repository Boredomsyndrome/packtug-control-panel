# WHEELTEC S2000 Robot — Setup Procedures (fleet, server-LAN mode)

Target: S200/S2000 brushless kit with onboard SBC (Jetson / Raspberry Pi /
LubanCat) running the WHEELTEC ROS2 (Ubuntu 22.04 / Humble) image.

**v2 architecture: the robot is a PURE CLIENT.** It turns its factory
hotspot OFF, joins the WiFi that the control laptop lives on, and only makes
outbound HTTP calls to the Django server. No rosbridge, no listening ports,
no per-robot IP juggling — and any number of robots share one server.

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

## 5. Install the robot client (once)

```bash
sudo apt install -y python3-requests       # only if the image lacks it
sudo cp ~/wheeltec-webctl/robotctl/wheeltec-robotctl.service /etc/systemd/system/
sudo cp ~/wheeltec-webctl/robotctl/robotctl.env.example /etc/wheeltec-robotctl.env
sudo nano /etc/wheeltec-robotctl.env       # WEBCTL_SERVER + this robot's ID/TOKEN
sudo systemctl daemon-reload
sudo systemctl enable --now wheeltec-robotctl
journalctl -u wheeltec-robotctl -f         # "robotctl client '<id>' -> http://..."
```

`/etc/wheeltec-robotctl.env` per robot: `WEBCTL_ROBOT_ID` +
`WEBCTL_ROBOT_TOKEN` must match one `WEBCTL_ROBOTS` entry on the server, and
`WEBCTL_SERVER` = `http://<laptop-ip-on-that-wifi>:8000`
(hotspot mode → `http://10.42.0.1:8000`).

**No rosbridge. No ROS ports exposed. Nothing to port-forward or firewall.**

## 6. Camera (optional, per robot)

The client relays whatever local MJPEG source you give it, so `web_video_server`
stays **loopback-only** (it is then a local pipe, not a server on the network):

`/etc/systemd/system/wheeltec-camera.service`:

```ini
[Unit]
Description=web_video_server bound to localhost only
After=wheeltec-ros.service

[Service]
Type=simple
User=wheeltec
ExecStartPre=/bin/sleep 20
ExecStart=/bin/bash -lc 'source /opt/ros/humble/setup.bash && [ -f ~/wheeltec_robot/install/setup.bash ] && source ~/wheeltec_robot/install/setup.bash; exec ros2 run web_video_server web_video_server --ros-args -p address:=127.0.0.1 -p port:=8080'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then in `/etc/wheeltec-robotctl.env` set (topic name from `ros2 topic list`):

```
WEBCTL_CAM_URL=http://127.0.0.1:8080/stream?topic=/camera/image_raw
```

Camera failure never affects driving — the client drops frames, keeps polling.

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

- **chip red / robot OFFLINE** → `journalctl -u wheeltec-robotctl` on the
  robot: `Connection refused` (server down / wrong IP), `401` (token
  mismatch vs `WEBCTL_ROBOTS`), TLS/DNS issues (use a plain IP + http on LAN).
- **robot joined but no IP** → router DHCP full / captive portal: school WiFi
  with captive portals is NOT supported (as before); laptop hotspot is.
- **hotspot came back after reboot** → an AP profile still has
  `connection.autoconnect yes`: rerun `scripts/robot_wifi_join.sh`.
- **moves then stops after ~1 s while holding a button** → your browser tab
  isn't focused/visible; heartbeat stopped — intended behavior.
- **camera unavailable, driving fine** → §6 not installed on that robot or
  wrong topic; check `journalctl -u wheeltec-camera`.
- **motors dead though UI says driving** → base driver (step 4) not running
  or MCU serial down: `ls -l /dev/wheeltec_controller*`.

## 10. Factory recovery / reverting

v2 robot-side footprint = one systemd unit, one env file, one NetworkManager
profile change, optional camera unit. Revert per robot:

```bash
sudo systemctl disable --now wheeltec-robotctl wheeltec-camera
sudo rm /etc/systemd/system/wheeltec-robotctl.service /etc/systemd/system/wheeltec-camera.service
sudo systemctl daemon-reload
sudo rm /etc/wheeltec-robotctl.env
nmcli connection modify "<AP name>" connection.autoconnect yes   # hotspot back
nmcli connection delete "<your wifi profile>"                     # optional
```

Nothing was ever installed on the STM32; factory `teleop_twist_keyboard`
still works to confirm the chassis is stock.

## Optional: drive over the internet later (home)

The same pull-mode works anywhere the robot has outbound HTTPS: run the
server behind HTTPS + Basic Auth (Caddy + DuckDNS like the rest of the
stack) and set `WEBCTL_SERVER=https://comet13579.duckdns.org/...`. Robots
dial out; still zero exposed robot ports. Keep clamps and watchdogs — and
expect 100–300 ms latency on cellular: crawl speed only.
