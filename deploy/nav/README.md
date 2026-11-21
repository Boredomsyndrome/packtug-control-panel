# deploy/nav/ — FROZEN REFERENCE (the teammate's client fork)

These files are the packtug-side robot fork exactly as it arrived at
upstream commit `ca265cc` (tag `v3.6.6-fusion-base`). They are kept as an
audit reference for the merge decision table — **they are NOT deployed and
NOT executed**. Do not follow any install recipe that used to live here.

The canonical, deployed robot plane is the single source of truth:

| deployed file            | lives in                     |
|--------------------------|------------------------------|
| client.py, navd.py       | `robotctl/`                  |
| navctl.sh                | `deploy/navctl.sh`           |
| webctl_scan_filter.py, webctl_tf_repeater.py | `robotctl/` |
| navigate_no_spin_backup.xml | `robotctl/nav_params/`    |

`tools/deploy_robot.py` uploads ONLY the canonical set above (+ run_client.sh
+ nav_params/*.yaml). Nothing in this directory is ever copied to a robot:
the fork's FASTRTPS export, EKF pkill and the three TF broadcasters were
REJECTED in the merge verdict (`docs/MERGE/`), while its direct map-save,
no-spin BT and mask widening were PORTED into the canonical files.

Generated-from header convention (CS-I2): every file here carries a
`generated-from:` line naming `ca265cc:deploy/nav/<file>`. If a future
decision adopts something from here (e.g. the fallback standalone odom
relay), port it into the canonical path with its own commit + tests —
never deploy from this directory.

Original inventory (as received at ca265cc):

- `navctl.sh` v3.6.6: filtered scan topics, single Nav2 owner, longer/retried
  map saving, no destructive `/dev/shm/fastrtps_*` cleanup, towed-cage Nav2
  tuning, no-spin/no-backup behavior tree, and a pre-started odom relay
- `client.py`: steady `/odom` TF relay (`odom_combined` → `base_footprint`)
  — REJECTED (client carries odom in telemetry only; EKF owns TF)
- `navd.py`: thread-safe TF/AMCL/map handling, direct map save, current
  Humble waypoint-result API — the P0 source for our `get_result_async` fix
- `webctl_scan_filter.py`: box mask, restamped `/scan_filtered`, wider default
  field of view, and odom TF mirror (mask widened via `WEBCTL_MASK_X_BACK`)
- `webctl_odom_relay.py`: standalone 20 Hz odom TF relay for C++ listeners
  — held as the ONLY fallback broadcaster (see merge verdict fallback clause)
- `webctl_tf_repeater.py`: laser TF repeater used by `navctl.sh`
- `navigate_no_spin_backup.xml`: recovery tree that only clears costmaps/waits
- `watchdog_vpn.sh`: parses both quoted and unquoted `WEBCTL_SERVER` values
