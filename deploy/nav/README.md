# Robot navigation deployment

These are deployable robot-side copies of the navigation changes, kept in this
repository so they do not depend on the separate `wheeltec-webctl` checkout.

Install on the Jetson:

```bash
cp navctl.sh watchdog_vpn.sh client.py navd.py \
   webctl_scan_filter.py webctl_odom_relay.py webctl_tf_repeater.py ~/robotctl/
mkdir -p ~/robotctl/nav_params
cp navigate_no_spin_backup.xml ~/robotctl/nav_params/
chmod +x ~/robotctl/navctl.sh ~/robotctl/watchdog_vpn.sh ~/robotctl/client.py \
  ~/robotctl/navd.py ~/robotctl/webctl_scan_filter.py \
  ~/robotctl/webctl_odom_relay.py ~/robotctl/webctl_tf_repeater.py
```

Do not overwrite `~/robotctl/robotctl.env` or `~/.config/wheeltec-robotctl.env`;
they contain the robot identity and token. Restart `run_client.sh` after
deploying `client.py`/`navd.py`.

Included changes:

- `navctl.sh` v3.6.6: filtered scan topics, single Nav2 owner, longer/retried
  map saving, no destructive `/dev/shm/fastrtps_*` cleanup, towed-cage Nav2
  tuning, no-spin/no-backup behavior tree, and a pre-started odom relay
- `client.py`: steady `/odom` TF relay (`odom_combined` → `base_footprint`)
- `navd.py`: thread-safe TF/AMCL/map handling, direct map save, current
  Humble waypoint-result API
- `webctl_scan_filter.py`: box mask, restamped `/scan_filtered`, wider default
  field of view, and odom TF mirror
- `webctl_odom_relay.py`: standalone 20 Hz odom TF relay for C++ listeners
- `webctl_tf_repeater.py`: laser TF repeater used by `navctl.sh`
- `navigate_no_spin_backup.xml`: recovery tree that only clears costmaps/waits
- `watchdog_vpn.sh`: parses both quoted and unquoted `WEBCTL_SERVER` values
