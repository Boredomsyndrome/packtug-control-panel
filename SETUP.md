# wheeltec-webctl — Setup Guide (zero → fleet driving)

One ordered procedure: laptop server first, robots second, verify, drive.
Architecture background → `README.md`; exhaustive robot-side detail
(power safety, factory recovery, ROS2 checks) → `ROBOT_SETUP.md`.

Time budget: ~15 min first robot (10 min more each extra robot), all commands
copy-pasteable. Two machines involved:

| | runs | address |
|---|---|---|
| **Laptop** (control PC) | Django server (the ONLY server) | `:8000` on the shared WiFi |
| **Each robot** | ROS2 base driver + `robotctl/client.py` | nothing — it calls OUT |

Both must end up on the SAME WiFi: either your router's LAN, or the **laptop's
own hotspot** (recommended for school demos — no internet needed, GNOME
hotspots always give the laptop `10.42.0.1`).

---

## Part 1 — Laptop server

### 1.1 Install (once)

```bash
cd ~/wheeltec-webctl
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cd frontend && npm ci && npm run build && cd ..        # node >= 18
cp .env.example .env
```

### 1.2 Register the fleet in `.env`

Generate one random token per robot:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # DJANGO_SECRET_KEY
```

```ini
# .env
WEBCTL_ROBOTS="s2000-1:<tokenA>,s2000-2:<tokenB>"   # id:token, comma-separated
MAX_LIN=0.5            # m/s hard clamp (server-side, always)
MAX_ROT=0.7            # rad/s hard clamp
CMD_TIMEOUT=0.5        # held cmd older than this -> zero served
DJANGO_SECRET_KEY=<th…>
DJANGO_DEBUG=false
ALLOWED_HOSTS=*
WEBCTL_AUTH_USER=      # set BOTH before any non-LAN/internet exposure
WEBCTL_AUTH_PASS=
```

Keep the token list handy — each robot gets exactly one `id:token` pair.

### 1.3 Self-test before touching hardware

```bash
.venv/bin/python -u tools/integration_test.py
# ... must print: RESULT: ALL CHECKS PASSED  (35 checks, self-cleaning)
bash tools/prod_smoke_test.sh        # optional: production launcher 8/8
```

### 1.4 Decide the shared network, note the server IP

* **Laptop hotspot (demo mode):** turn it on; robots join it like any WiFi;
  server address is `http://10.42.0.1:8000` (KDE/other hotspots: check `ip a`).
* **Router LAN (home):** give the laptop a **DHCP reservation** in the router
  so its address never changes → that's `WEBCTL_SERVER` for all robots.

### 1.5 Run the server (exactly ONE process)

```bash
tmux new -s webctl -d '.venv/bin/python backend/manage.py runserver 0.0.0.0:8000'
tmux attach -t webctl          # Ctrl-b d to detach;  tmux kill-session -t webctl to stop
```

The `.server.lock` flock makes any second server answer 503 — that's a
feature, kill the old one instead of running two.

---

## Part 2 — Each robot (repeat per robot)

Details + first-time-only steps: `ROBOT_SETUP.md` §1–§6. Condensed:

### 2.1 Power-on check (every session)

Physical e-stop pressed → battery 25–29 V (charge below ~24 V) → wheels clear
of the ground/cable until first drive. Release e-stop only when ready.

### 2.2 Get onto the shell (first time only, via the factory hotspot)

Join hotspot `WHEELTEC_CAR_…` from the laptop (password per kit manual,
often `dongguan`), then `ssh wheeltec@192.168.0.100`. Later visits:
`ssh wheeltec@<robot's-new-WiFi-IP>`.

```bash
# copy the project onto the robot (from the laptop, over whichever link works)
scp -r ~/wheeltec-webctl wheeltec@192.168.0.100:~/   # first time via AP
# or, after it's on the LAN once:
rsync -a --exclude .venv --exclude node_modules --exclude .logs \
      ~/wheeltec-webctl/ wheeltec@<robot-ip>:~/wheeltec-webctl/
```

### 2.3 Hotspot OFF → join the server WiFi (persists across reboots)

```bash
cd ~/wheeltec-webctl
sudo bash scripts/robot_wifi_join.sh "<YOUR_SSID>" '<PSK>'
```

The script joins as a client (autoconnect, top priority) and disables every
AP-mode NetworkManager profile. Note the robot IP it prints. Revert anytime:
`nmcli connection modify "<AP name>" connection.autoconnect yes && reboot`.

### 2.4 Base driver (the STM32 bridge) — verify running

```bash
source /opt/ros/humble/setup.bash
[ -f ~/wheeltec_robot/install/setup.bash ] && source ~/wheeltec_robot/install/setup.bash
ros2 launch turn_on_wheeltec_robot turn_on_wheeltec_robot.launch.py   # or your kit's bringup
# second terminal:
ros2 topic echo /PowerVoltage --once      # a voltage prints => MCU link OK
```

Autostart this on boot the same way as §2.5 (the client unit waits for it).

### 2.5 Install the robot client (once, autostarts forever)

```bash
sudo apt install -y python3-requests      # skip if `python3 -c "import requests"` works
sudo cp ~/wheeltec-webctl/robotctl/wheeltec-robotctl.service /etc/systemd/system/
sudo cp ~/wheeltec-webctl/robotctl/robotctl.env.example /etc/wheeltec-robotctl.env
sudo nano /etc/wheeltec-robotctl.env
```

`/etc/wheeltec-robotctl.env` — the four values that matter:

```ini
WEBCTL_SERVER=http://10.42.0.1:8000      # laptop from Part 1.4 (hotspot example)
WEBCTL_ROBOT_ID=s2000-1                  # must match a WEBCTL_ROBOTS entry
WEBCTL_ROBOT_TOKEN=<tha…ken>            # ...and its token
WEBCTL_WATCHDOG=1.0                      # no poll this long -> motors zero
# WEBCTL_CAM_URL=... only if this robot has a camera (ROBOT_SETUP.md §6)
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wheeltec-robotctl
journalctl -u wheeltec-robotctl -f       # want: "robotctl client 's2000-1' -> http://..."
```

---

## Part 3 — First drive

1. On the robot, confirm the link it-self:

   ```bash
   curl -su "s2000-1:<tokenA>" "http://<server>:8000/robotctl/s2000-1/cmd?wait=0"
   # {"ok": true, "vx": 0.0, ...} = robot reaches server
   ```

2. Open `http://<server>:8000` in a browser (phone on same WiFi works too).
   The robot's chip turns **green ≤ 2 s**; battery voltage appears.
3. Wheels in the air → press ▲ → wheel spins → release → stops within ~0.5 s.
4. Yank the robot's WiFi / close the tab → chip red, and it already stopped
   on its own within ~1 s via the onboard watchdog.
5. Add robot #2: just repeat Part 2 with `s2000-2:<tokenB>`. Two chips, two
   robots, one page — select a chip to switch; commands never cross.

**Demo day:** batteries ≥25 V, e-stops reachable, one server only, power-cycle
each robot beforehand (chip must go green by itself ≤90 s), integration test
green before packing (README §C).

---

## Troubleshooting quick table

| symptom | fix |
|---|---|
| chip stays red / OFFLINE | `journalctl -u wheeltec-robotctl` → connection refused = wrong `WEBCTL_SERVER`/server down; 401 = token mismatch vs `.env` |
| joined WiFi but no IP | router DHCP/captive portal — school WiFi unsupported; use laptop hotspot |
| drives ~1 s then stops | browser tab unfocused/hidden → heartbeat stopped (safety feature, focus the tab) |
| UI drives, motors silent | base driver (§2.4) not running, or MCU serial down (`ls -l /dev/wheeltec_controller*`) |
| camera "unavailable" | that robot has no `WEBCTL_CAM_URL` / web_video_server down — driving unaffected |
| 503 "does not own" | two server processes; keep one (`tmux ls`, kill extras) |

## Reverting / starting over

* Robot side: `ROBOT_SETUP.md` §10 — one unit, one env file, one WiFi profile;
  STM32 never touched, stock `teleop_twist_keyboard` still works.
* Server: `.env` is the only state; delete and re-copy `.env.example`.
* Old hotspot workflow kept for archaeology: git tag `v1-baseline`.
