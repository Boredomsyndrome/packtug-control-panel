#!/usr/bin/env bash
# Turn a WHEELTEC SBC from the factory hotspot (AP mode) into a WiFi CLIENT
# (STA) on the LAN where the FastAPI webctl hub runs. After this,
# the robot broadcasts NOTHING and only makes outbound HTTP calls.
#
#   sudo ./robot_wifi_join.sh <SSID> <PASSWORD> [iface=wlan0]
#
# The factory AP profile is DISABLED (autoconnect off, never deleted), so
# the robot rejoins your WiFi on every boot. Revert any time:
#   nmcli connection modify "<AP name>" connection.autoconnect yes && reboot
set -euo pipefail
SSID="${1:?usage: robot_wifi_join.sh <SSID> <PASSWORD> [iface=wlan0]}"
PSK="${2:?missing password}"
IFACE="${3:-wlan0}"

command -v nmcli >/dev/null || { echo "nmcli (NetworkManager) not found"; exit 1; }

echo "[1/3] joining '$SSID' on $IFACE ..."
nmcli device wifi connect "$SSID" password "$PSK" ifname "$IFACE"

# remember + prefer the profile NetworkManager just activated on this iface
CON="$(nmcli -g NAME,DEVICE connection show --active | grep ":${IFACE}\$" | head -1 | cut -d: -f1 || true)"
if [ -n "${CON:-}" ]; then
  nmcli connection modify "$CON" connection.autoconnect yes \
      connection.autoconnect-priority 100
  echo "      using profile '$CON' (autoconnect on, priority 100)"
fi

echo "[2/3] disabling AP-mode (hotspot) profiles ..."
found=0
while IFS= read -r name; do
  [ -z "$name" ] && continue
  mode="$(nmcli -f 802-11-wireless.mode -g 802-11-wireless.mode connection show "$name" 2>/dev/null || true)"
  if [ "$mode" = "ap" ]; then
    nmcli connection modify "$name" connection.autoconnect no
    echo "      hotspot profile disabled: $name"
    found=1
  fi
done < <(nmcli -t -f NAME connection show)
[ "$found" = 1 ] || echo "      (no AP profiles found — already client-only)"

echo "[3/3] current address:"
ip -4 -br addr show "$IFACE" || true
echo
echo "Done. Point this robot's client env at the server, e.g.:"
echo "  WEBCTL_SERVER=http://<server-ip-on-this-lan>:8000"
echo "(server laptop: fix its address with a DHCP reservation on the router,"
echo " or run the server on the laptop's own hotspot — GNOME hotspots use"
echo " 10.42.0.0/24 and the laptop itself is 10.42.0.1)"
