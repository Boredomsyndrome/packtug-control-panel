# Robot navigation deployment

These are deployable robot-side copies of the navigation changes, kept in this
repository so they do not depend on the separate `wheeltec-webctl` checkout.

Install on the Jetson:

```bash
cp navctl.sh watchdog_vpn.sh client.py navd.py ~/robotctl/
chmod +x ~/robotctl/navctl.sh ~/robotctl/watchdog_vpn.sh ~/robotctl/client.py ~/robotctl/navd.py
```

Do not overwrite `~/robotctl/robotctl.env` or `~/.config/wheeltec-robotctl.env`;
they contain the robot identity and token. Restart `run_client.sh` after
deploying `client.py`/`navd.py`.

Included changes:

- `navctl.sh` v3.6.6: filtered scan topics, single Nav2 owner, longer/retried
  map saving, no destructive `/dev/shm/fastrtps_*` cleanup
- `client.py`: steady `/odom` TF relay (`odom_combined` → `base_footprint`)
- `navd.py`: thread-safe TF/AMCL/map handling, direct map save, current
  Humble waypoint-result API
- `watchdog_vpn.sh`: parses both quoted and unquoted `WEBCTL_SERVER` values
