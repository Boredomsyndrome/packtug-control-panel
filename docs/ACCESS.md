# Getting robots & browsers to the potatoserver

The server binds `0.0.0.0:8090` on potatoserver (`192.168.1.142`, USB-eth).
The robot currently lives on a different LAN segment (`192.168.0.x`) and
NAT/firewalls mean it can reach the *internet* but not potatoserver
directly. Three ways in — **A0 (already installed, start here)**; A is a
modern alternative; B is for browser access without a VPN client:

## Option A0 — the EXISTING IPsec VPN (already live on this box)

strongSwan + xl2tpd are running (`~/vpn/vpn.sh`, hwdsl2-style):

| transport | robot gets | reach server at |
|---|---|---|
| L2TP/IPsec (UDP 500/4500/1701) | pool `192.168.42.10-250` | **`192.168.42.1`** |
| IKEv2 EAP (UDP 500/4500) | pool `192.168.43.10-250` | **`192.168.43.1`** |

Both addresses are already in `.env` `ALLOWED_HOSTS` (verified 200 + full
E2E). So the robot recipe is:

1. Manage users with `~/vpn/add_vpn_user.sh '<user>' '<pass>'`
   (IKEv2/L2TP creds live in the IPsec secrets files; PSK too).
2. On the Jetson create an **L2TP/IPsec** (simplest via
   `nm-connection-editor`) or IKEv2 profile: gateway = potatoserver's
   **public** address (DuckDNS IP), PSK + user/pass from step 1.
3. Robot env: `WEBCTL_SERVER=http://192.168.42.1:8090`
   (or `192.168.43.1` on IKEv2).
4. Router must forward **UDP 500, 4500, 1701 + ESP(proto 50)** to
   `192.168.1.142` — note the old setup history used `192.168.1.141`;
   if the port-forward still points there, repoint it (the box's DHCP
   address moved to .142).
5. While debugging, watch from this box: `journalctl -u xl2tpd -f` and
   `sudo ipsec statusall` — an established SA appears as `ipsec0`/`ppp0`
   with the tunnel IPs; nothing shows while no client is connected
   (that is normal — `192.168.42.1` is a *tunnel-side* address, not a
   domain or an idle interface).

## Option A — Tailscale VPN (alternative, ~5 min per machine)

```bash
# ON POTATOSERVER (root)
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up            # opens a login URL; sign into your tailnet
tailscale ip -4              # note the 100.x.y.z address

# ON THE ROBOT (Jetson, has internet now)
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ping <potato-100.x.y.z>   # must answer before continuing
```

Then:
1. potatoserver `.env`: append the **robot's** 100.x IP to `ALLOWED_HOSTS`
   (and your laptop's if you browse via tailscale too), then
   `tmux kill-session -t webctl` + relaunch (see docs/API.md §5).
2. Robot env (`~/.config/wheeltec-robotctl.env`):
   `WEBCTL_SERVER=http://<potato-100.x.y.z>:8090`
   Traffic is WireGuard-encrypted end-to-end, so plain http:// over the
   tailnet is fine — no cert hassle.
3. Browser: install Tailscale on the laptop and open
   `http://<potato-100.x.y.z>:8090` straight away.

## Option B — HTTPS via DuckDNS + Caddy (for real WAN without a VPN client)

Caddy is not yet installed here; run as root:

```bash
sudo apt install -y caddy
sudo mkdir -p /etc/letsencrypt
sudo tee /etc/letsencrypt/duckdns.ini >/dev/null <<'EOF'
dns_duckdns_token=YOUR_DUCKDNS_TOKEN
EOF
sudo chmod 600 /etc/letsencrypt/duckdns.ini
sudo pip3 install certbot-dns-duckdns --break-system-packages
sudo certbot certonly -a dns-duckdns \
  --dns-duckdns-credentials /etc/letsencrypt/duckdns.ini \
  -d <your-domain>
sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-caddy.sh >/dev/null <<'EOF'
#!/bin/bash
systemctl reload caddy
EOF
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-caddy.sh
sudo cp ~/wheeltec-webctl/deploy/Caddyfile.potatoserver.example /etc/caddy/Caddyfile
sudo systemctl enable --now caddy && sudo caddy reload
```

Router: forward external **8443 → 192.168.1.142:8443**. Then
`https://<your-domain>:8443` serves everything (Caddy proxies
http→8090 and handles WebSocket upgrades automatically). Add
`<your-domain>` to `ALLOWED_HOSTS` (already in `.env`).
Robot uses the same https URL as `WEBCTL_SERVER` — always use https here,
the robot token rides in Basic auth.

DNS-01 (certbot above) means ports 80/443 stay untouched — those are
foreign root services on this box. DuckDNS sub-subdomains resolve to the
same IP, so `webctl.<your-domain>` also works if you later split
services.
