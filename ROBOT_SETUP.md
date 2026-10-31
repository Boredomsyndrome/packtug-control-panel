# WHEELTEC S2000 Robot — Setup Procedures (school demo)

Step-by-step: power to first web-page drive. Target: S200/S2000 brushless kit
with onboard SBC (Jetson / Raspberry Pi / LubanCat) running the WHEELTEC ROS2
(Ubuntu 22.04 / Humble) image. No router needed — the robot creates its own
WiFi hotspot.

```
 [S2000 chassis]──UART──[SBC: ROS2]──WiFi AP── [your laptop = webctl server]──browser
   hub motors/ESCs       STM32 base node        192.168.0.100     192.168.0.x :8000
```

## 0. What to bring

- Robot (battery charged!), 24 V charger
- Control laptop (Linux, or Windows with WSL2) with WiFi
- Monitor + keyboard (only if you need to fix the SBC directly)
- Optional: power strip, phone (joins same hotspot = extra controller)

## 1. Power-on safety check

1. Confirm physical **e-stop** pressed; wheels off the ground / clear area ≥2 m.
2. Battery pack connected; check voltage on the display or later via the web page
   (healthy 24 V LiFePO4 ≈ 25–29 V; below ~24 V → charge first).
3. Release e-stop only right before driving.

## 2. Boot the robot (AP mode = its own hotspot)

1. Flip the power switch; wait ~60–90 s for the SBC to finish booting.
2. On your laptop, open WiFi and join the robot's hotspot:
   SSID pattern `WHEELTEC_CAR_…` (trailing number = unit id),
   password from your kit's manual (commonly `dongguan`).
3. The SBC keeps static IP **192.168.0.100**; your laptop gets `192.168.0.x`
   via DHCP. This is a closed network — no internet, nothing else needs it.

> If no hotspot appears: connect a monitor; check the network icon, or run
> `nmtui` on the SBC and make sure the hotspot/AP connection is active.

## 3. SSH into the SBC and check the ROS2 environment

```bash
ssh wheeltec@192.168.0.100        # user: wheeltec or ubuntu — see manual
```

```bash
# ROS2 Humble comes from the image; the robot workspace is usually:
source /opt/ros/humble/setup.bash
source ~/wheeltec_robot/install/setup.bash    # if this dir exists (most kits)
ros2 pkg list | grep turn_on_wheeltec_robot   # confirm the base-driver package
```

If the grep prints nothing, find the package with
`ros2 pkg list | grep -iE 'wheeltec|turn_on'` and use that name below.

## 4. Start the base driver (MCU bridge)

```bash
ros2 launch turn_on_wheeltec_robot turn_on_wheeltec_robot.launch.py
```

(launch file names differ slightly per model — press TAB after
`ros2 launch turn_on_wheeltec_robot ` to see what your image ships.)

Verify in a second SSH terminal (source the same two lines first):

```bash
ros2 topic list          # expect: /cmd_vel, /PowerVoltage, /odom, /imu …
ros2 topic echo /PowerVoltage --once    # prints battery voltage → MCU link OK
```

## 5. Install the web stack (once)

```bash
sudo apt update
sudo apt install -y ros-humble-rosbridge-suite ros-humble-web-video-server
```

## 6. Run rosbridge + web_video_server

Manual (good for the first time):

```bash
ros2 launch rosbridge_server rosbridge_websocket_launch.xml &   # WebSocket :9090
ros2 run web_video_server web_video_server --ros-args -p port:=8080 &  # MJPEG :8080
ros2 topic list   # confirm your camera topic name for step 8
```

### Autostart on boot (after step 7 works once)

`/etc/systemd/system/wheeltec-ros.service`:

```ini
[Unit]
Description=wheeltec base bringup + rosbridge + web_video_server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=wheeltec                       # match your SBC user
Environment=ROS_DOMAIN_ID=0
ExecStartPre=/bin/sleep 25
ExecStart=/bin/bash -lc 'source /opt/ros/humble/setup.bash && [ -f ~/wheeltec_robot/install/setup.bash ] && source ~/wheeltec_robot/install/setup.bash; ros2 launch turn_on_wheeltec_robot turn_on_wheeltec_robot.launch.py'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/wheeltec-web-stack.service`: same file but
`ExecStart=... ros2 launch rosbridge_server rosbridge_websocket_launch.xml`
(add `After=wheeltec-ros.service`). web_video_server: a third unit with
`ExecStart=/bin/bash -lc 'source /opt/ros/humble/setup.bash; ros2 run web_video_server web_video_server --ros-args -p port:=8080'`.

Enable with:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wheeltec-ros wheeltec-web-stack
```

(Prefer tmux over systemd? `tmux new -s ros -d '…'` per terminal also works.)

## 7. Control server (your laptop)

From the `wheeltec-webctl/` folder (see README.md there):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cd frontend && npm ci && npm run build && cd ..
cp .env.example .env      # defaults already match AP mode (robot = 192.168.0.100)
```

`.env` in AP mode: `ROBOT_WS_URL=ws://192.168.0.100:9090`,
`CAMERA_BASE_URL=http://192.168.0.100:8080`, set `CAMERA_TOPIC` to what
`ros2 topic list` showed (e.g. `/camera/image_raw`), set
`WEBCTL_AUTH_USER`/`WEBCTL_AUTH_PASS` if classmates' devices join the AP,
and give `DJANGO_SECRET_KEY` a random string.

Sanity-check the server itself (no robot needed):

```bash
.venv/bin/python -u tools/integration_test.py   # expect ALL CHECKS PASSED
```

Start (exactly one process):

```bash
tmux new -s webctl -d '.venv/bin/python backend/manage.py runserver 0.0.0.0:8000'
```

## 8. First drive

1. Laptop browser → `http://127.0.0.1:8000` — badge must show **robot connected**.
2. Battery voltage appears → STM32 link is alive.
3. Wheels in the air first: tap ▲ — hub motors should spin; speed preset 🐢 crawl
   is the default demo speed.
4. Put it down; drive with ▲▼◀▶. Release → stops within 0.5 s (watchdog).
5. Space or ■ E-STOP → dead stop, latched; CLEAR E-STOP button or any new
   command resumes.
6. Optional: phone joins the same hotspot → open `http://<laptop-ip>:8000`
   (find laptop IP: `hostname -I`) → on-screen buttons work there too.

## 9. Demo-day checklist

- [ ] Battery ≥ 25 V charged overnight
- [ ] Home test passed (hotspot join → page → drive 30 s)
- [ ] `integration_test.py` passes on the laptop
- [ ] Autostart units on (unplug/replug power, wait 2 min, page connects by itself)
- [ ] Physical e-stop reachable at the demo table
- [ ] Laptop volume/notification off; browser tab pinned full-screen

## 10. Troubleshooting

- **Hotspot missing** → monitor + `nmtui` on the SBC; AP connection may have
  been deleted; WHEELTEC manuals re-create it in ~2 min.
- **Page shows robot OFFLINE** → from laptop: `curl -s http://192.168.0.100:8080`
  (proves routing to SBC). If reachable: rosbridge not running on robot —
  `ssh wheeltec@192.168.0.100` and start step 6; check
  `sudo systemctl status wheeltec-web-stack`.
- **Connected but motors dead** → base driver (step 4) not running, or MCU
  serial not up: `ls -l /dev/wheeltec_controller*` on the SBC.
- **`/PowerVoltage` empty** → MCU power/board off; check the chassis switch.
- **Camera 502 on page** → wrong `CAMERA_TOPIC`; copy the exact name from
  `ros2 topic list`.
- **School WiFi instead of hotspot** → do not: captive portals + client
  isolation break WebSockets/multicast. AP mode is the plan.

## Optional: control over the internet later (home)

Install Tailscale on the SBC and your computers; use
`ROBOT_WS_URL=wss://…` only through an authenticated tunnel — never expose
rosbridge (9090) directly to the internet.
