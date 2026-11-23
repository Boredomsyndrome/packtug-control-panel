# robot/ — everything the S2000 Jetson runs

This whole folder is the robot deployment: `tools/deploy_robot.py` ships it
byte-for-byte to `~/robotctl/` on the Jetson (the live `robotctl.env`,
`maps/` and lock files on the robot are never overwritten). Nothing here is
installed or containerized: the client is stdlib-only, rclpy/cv2 come from
the stock ROS 2 Humble image.

**One command runs every robot function:**

```bash
bash ~/robotctl/start-all.sh     # idempotent; @reboot crontab for boot
```

| file | function |
| --- | --- |
| `start-all.sh` | bring-up: base serial node, LiDAR, ASTRA S, client (spawns navd when `WEBCTL_NAV=1`); reports camera/watchdog status |
| `client.py` | the ONE websocket uplink: telemetry, /scan, camera JPEG, cmd consume, nav mailbox. Run directly or via `run_client.sh` (sources ROS + env, used by `wheeltec-robotctl.service`) |
| `navd.py`, `navctl.sh`, `nav_params/`, `ekf_navctl.yaml` | autonomy plane: slam_toolbox mapping, Nav2 localize/route (docs/NAV.md) |
| `webctl_scan_filter.py`, `webctl_tf_repeater.py` | rear-mask widening on /scan→/scan_filtered; odom TF bridge for the cage |
| `camera_stream.py` | D455 RGB MJPEG bridge :8080 — systemd `packtug-camera.service` (install: `sudo robot/setup_camera_service.sh`; watchdog timer: `setup_camera_watchdog.sh`) |
| `setup_arm_link.sh` | NetworkManager `arm-link` profile (UGREEN → Ned1, 100 Mb/s FD) |
| `watchdog_lidar.sh`, `watchdog_vpn.sh` | cron resilience units (crontab lines printed by start-all.sh) |
| `wifi_join.sh` | factory AP → WiFi STA mode once |
| `robotctl.env.example` | copy to `~/.config/wheeltec-robotctl.env` (chmod 600) — SERVER list, ID, TOKEN, `WEBCTL_NAV=1` |
| `requirements.txt` | signpost: ZERO dependencies by design |

Paths on the robot (`~/robotctl/...`) and the `/robotctl/<id>/...` hub API
URLs are wire contracts — they keep the old name even though the repo folder
is now `robot/`.
