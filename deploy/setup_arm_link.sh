#!/usr/bin/env bash
set -euo pipefail

IFACE="${1:-eth0}"
CON="${2:-arm-link}"
ADDRESS="169.254.200.1/16"

if ! nmcli -t -f NAME connection show | grep -qx "$CON"; then
  sudo nmcli connection add \
    type ethernet \
    ifname "$IFACE" \
    con-name "$CON" \
    ipv4.method manual \
    ipv4.addresses "$ADDRESS" \
    ipv4.never-default yes \
    ipv6.method ignore \
    connection.autoconnect yes
fi

sudo nmcli connection modify "$CON" \
  connection.interface-name "$IFACE" \
  ipv4.method manual \
  ipv4.addresses "$ADDRESS" \
  ipv4.never-default yes \
  ipv6.method ignore \
  connection.autoconnect yes \
  802-3-ethernet.speed 100 \
  802-3-ethernet.duplex full \
  802-3-ethernet.auto-negotiate no

sudo nmcli connection up "$CON"
