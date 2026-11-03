# School day setup — bring-your-own router (no school WiFi, no internet)

The whole fleet runs on **your own router, WAN port empty**. The school's
network is never touched — school WiFi is captive-portal/client-isolation
soup and would break everything; an offline router is a clean private LAN.
This is the same pattern FRC teams use for field networks.

Why a router + this app is almost too easy: the app is **pull-mode** — robots
dial the server, never the other way round. So *only the laptop's IP needs to
be stable* (one DHCP reservation); every robot just grabs whatever DHCP lease
it likes, forever, even after being power-cycled a hundred times.

```
 school power outlet                     nothing else, no internet
      │
  ┌───▼───────────── router (LAN 192.168.50.0/24, WAN unplugged) ──────┐
  │  SSID: WT-FLEET (WPA2-PSK)                                         │
  │   ├─ laptop 192.168.50.50  ← DHCP RESERVATION (the only fixed IP)  │
  │   │                 Django :8000  ◀── browser UIs (phones too)     │
  │   ├─ robot s2000-1 (any DHCP lease) ── ws to server (http fallback) ─▶ │
  │   └─ robot s2000-2 (any DHCP lease) ── telemetry + frames ─▶       │
  └─────────────────────────────────────────────────────────────────────┘
```

**The trick: configure everything at home on this same router. Then school
day = plug in power. Zero reconfiguration, zero school dependencies.**

---

## Part A — At home, once: the router

Log into the admin page (usually `192.168.0.1` / `192.168.1.1` /
`tplinkwifi.net` etc. — check the router's sticker).

1. **Never connect the WAN/Internet port. Ever.** Not at home, not at school.
   A "no internet" warning on the admin page is expected and harmless —
   everything we use (DHCP, DNS-less IP traffic, Wi-Fi) lives on the LAN side.
2. **LAN side**
   - Router LAN IP: `192.168.50.1`, mask `255.255.255.0` (an unusual subnet on
     purpose — it can't collide with anything school-side if a cable is ever
     plugged by mistake).
   - **DHCP server: ON**, pool like `192.168.50.100–200`.
3. **DHCP reservation for the laptop** — Admin → DHCP → Address Reservation:
   bind the **laptop's Wi-Fi MAC** (Ubuntu: `ip -br link | grep wlan`, or
   `cat /sys/class/net/wlan0/address`) to **`192.168.50.50`**.
   This address becomes `WEBCTL_SERVER` for every robot, forever.
4. **Wi-Fi**
   - SSID `WT-FLEET`, **WPA2-PSK (AES) only** — no open, no WPA3-only, no
     802.1X/enterprise; plain password ≤ 30 chars, ASCII, no spaces
     (NetworkManager + wpa_supplicant across boards is happiest with this).
   - **Disable Guest network** and any **AP/Client Isolation** setting (often
     called "Client Isolation", "AP Isolation", "Wireless Isolation" —
     sometimes under Wireless Advanced or Guest settings). If isolation stays
     on, robots and phones can join but can never reach the laptop — the
     classic "everything connects but nothing works" symptom.
   - 2.4 GHz channel: set **1, 6 or 11 manually** (schools are the densest
     2.4 GHz environments on the planet; Auto rarely survives it). Pick one,
     note it, and keep it at home too so behavior matches.
   - Dual-band router: keep both bands on, one SSID is fine; if a robot ever
     misbehaves, split bands and pin robots to 2.4 GHz.
5. Leave any "time sync / NTP / dynamic DNS / cloud" settings alone — they'll
   silently fail with no WAN. Nothing in the stack cares.
6. **Save + reboot the router once**, then verify from the laptop:
   join `WT-FLEET`, `ip -4 addr show` shows `192.168.50.50`.

## Part B — At home, once: point the fleet at the router

Server (laptop, `~/wheeltec-webctl`):

```bash
source .venv/bin/activate
.venv/bin/python -u tools/integration_test.py     # ALL CHECKS PASSED, sanity
```

Nothing on the server binds to a specific IP (`0.0.0.0:8000`), so the server
side needs **no change** — just remember the laptop IP is now
`http://192.168.50.50:8000`.

Each robot — join it to `WT-FLEET` and point the client at the reservation
(run on the robot; see SETUP.md §2.2 for getting onto the shell):

```bash
cd ~/wheeltec-webctl
sudo bash scripts/robot_wifi_join.sh "WT-FLEET" '<router-psk>'
mkdir -p ~/.config && cp robotctl/robotctl.env.example ~/.config/wheeltec-robotctl.env
chmod 600 ~/.config/wheeltec-robotctl.env
nano ~/.config/wheeltec-robotctl.env          # values below
```

```ini
WEBCTL_SERVER=http://192.168.50.50:8000      # the reservation — same at home & school
WEBCTL_ROBOT_ID=s2000-1                       # + its token, as in server .env
```

```bash
tmux new -s robotctl 'bash ~/wheeltec-webctl/robotctl/run_client.sh'
# want: websocket up -> ws://192.168.50.50:8000/ws/robotctl/s2000-1
# (zero installs — the robot's internet is never needed; boot autostart:
#  desktop autostart entry per ROBOT_SETUP.md §5)
```

Do this at home **while the router is also powered at home** — the robots save
the SSID profile, and at school they'll reconnect to it the moment it appears.
This is the whole magic of bringing the router: the school environment is
byte-for-byte identical to your home test bench.

Camera robots: keep `WEBCTL_CAM_WIDTH=640` (ROBOT_SETUP.md §6) — 1920 px
video over congested school 2.4 GHz gets choppy; 640 stays smooth.

## Part C — Pre-flight (night before)

- [ ] Router + **power strip/extra adapter** + laptop charger in the bag
- [ ] Router test at home: WAN unplugged, laptop joins → gets `192.168.50.50`
- [ ] Server self-test green: `.venv/bin/python -u tools/integration_test.py`
- [ ] Each robot: joined `WT-FLEET`, client autostarted or run command
      written down, batteries charged (25–29 V)
- [ ] Full home dress rehearsal: router + server + all robots → all chips
      green in the UI, drive each 10 s, yank one robot's power → stops
- [ ] Laptop firewall not blocking: `sudo ufw status` — if `active`:
      `sudo ufw allow from 192.168.50.0/24 to any port 8000 proto tcp`
      (Ubuntu desktop default is inactive = nothing to do)
- [ ] If the laptop carries school-proxy env vars (`env | grep -i proxy`):
      `export NO_PROXY=192.168.50.0/24,10.42.0.0/24` before starting the
      server — `requests`-based tools honor proxies and LAN calls get eaten.

## Part D — At school (the 3-minute startup)

1. Power the **router** only. Wait ~30 s for Wi-Fi to come up.
   **Do not connect it to anything. Power is the only cable.**
2. Laptop joins `WT-FLEET` → confirm `ip -4 addr show` shows `192.168.50.50`
   ("No internet" icon is EXPECTED and correct — ignore every captive-portal
   popup; on Windows also answer *Yes/private network* to the discoverable
   prompt, its firewall blocks inbound 8000 otherwise).
3. Start the server: `tmux new -d -s webctl 'bash scripts/run_server.sh
   0.0.0.0 8000'` (from `~/wheeltec-webctl`) — uvicorn ASGI, WebSockets on.
4. Power robots (base driver autostart per ROBOT_SETUP.md; client per Part B
   — else `tmux new -s robotctl 'bash ~/wheeltec-webctl/robotctl/run_client.sh'`).
   Within ~90 s all chips green + transport `ws` at `http://192.168.50.50:8000`.
5. E-stops reachable, wheels up for first spin, go.

## Part E — School-only failure modes

| symptom | cause → fix |
|---|---|
| chip red, robot has IP (client log clean) | laptop's IP isn't `:50` anymore — `ip -4 addr show`; rejoin `WT-FLEET`; check the reservation MAC matches `cat /sys/class/net/wlan0/address` |
| robot gets `192.168.50.x` but can't reach laptop | client isolation ON, or joined the router's **guest** SSID → Part A.4 |
| robot joined school WiFi instead | it has a saved school profile with better priority — `nmcli -f NAME,TYPE connection show --active` on the robot; delete school profile or raise WT-FLEET priority (script already sets 100) |
| everything green, camera choppy/sluggish | 2.4 GHz congestion — move channel (1/6/11), drop cam width to 640, stand router nearer robots than laptop; control itself is ~400 B/poll and survives almost anything |
| works at home, dead at school: nothing gets IPs | router DHCP died / WAN-standby quirk on some ISP boxes: reboot router, wait 30 s; verify from laptop `dhclient` retries |
| laptop fine, phones can't open the page | phones joined school WiFi out of habit → join `WT-FLEET`; or isolation (Part A.4) |
| "connected, no internet" worries bystanders/you | cosmetic only — nothing in this stack uses DNS, NTP, or the WAN |

Fallback if the router dies at school: the laptop's own hotspot still works
exactly like before (SETUP.md §1.4) — but robots would need their
`WEBCTL_SERVER` repointed to `http://10.42.0.1:8000` and a restart. Add a
second profile line to `.env`/unit env if you want both armed; otherwise this
stays Plan B.
