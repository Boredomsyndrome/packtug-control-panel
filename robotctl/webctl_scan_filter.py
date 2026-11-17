#!/usr/bin/env python3
"""webctl_scan_filter — /scan -> /scan_filtered (vendor-proven pre-slam stage).

WHY (2026-11-17): the vendor stack runs EVERY slam/nav2 consumer on
/scan_filtered, never raw /scan (vendor GitHub: prod_bringup +
mapper_params_rplidar_a1.yaml + nav2 param yamls). The robot's vendor build
lacks the vendor filter binary and apt installs are forbidden, so this is
the stock-ROS2 equivalent. THREE jobs (each one matters — see comments):
 1. box-mask the chassis returns (vendor laser_filter.yaml math),
 2. re-publish RELIABLE depth-10 (slam's message_filter subscriber uses
    rmw_qos_profile_sensor_data=best_effort, compatible with a reliable
    publisher; reliable also keeps history for late joiners),
 3. re-stamp scans to the ODOM timeline: slam's tf2 MessageFilter target is
    odom_frame at the EXACT scan stamp (humble slam_toolbox_common.cpp:243)
    and the EKF's tf stamps lag system time by ~26-50 ms — scans stamped at
    'now' sit past the newest odom bracket, wait out their 0.2 s tolerance,
    and slam's failure-cache overflows: 'queue is full' at 100%.
"""
import sys

import numpy as np
import rclpy
from builtin_interfaces.msg import Time
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan
from tf2_msgs.msg import TFMessage


class ScanFilter(Node):
    def __init__(self, min_x, max_x, min_y, max_y):
        super().__init__("scan_box_filter_node")
        self.box = (min_x, max_x, min_y, max_y)
        # laser frame offset (same constant as webctl_tf_repeater / vendor TF)
        self.ox, self.oy = 0.157, 0.0
        self.pub = self.create_publisher(LaserScan, "/scan_filtered", 10)
        self.sub = self.create_subscription(
            LaserScan, "/scan", self.cb, qos_profile_sensor_data)
        # odom-stamp lag learner: median (arrival - header.stamp) of
        # odom_combined->base_footprint transforms (the EKF's own lateness)
        self._lag = 0.05  # seed = observed; auto-corrected below
        self._lags = []
        self._odom_seen = 0
        self.create_subscription(TFMessage, "/tf", self._odomcb, 100)
        self.get_logger().info(
            "webctl scan filter: /scan -> /scan_filtered box "
            "x[%.2f,%.2f] y[%.2f,%.2f]" % self.box)

    def _odomcb(self, msg):
        for t in msg.transforms:
            if (t.child_frame_id == "base_footprint"
                    and t.header.frame_id == "odom_combined"):
                arr = self.get_clock().now().nanoseconds * 1e-9
                st = t.header.stamp.sec + t.header.stamp.nanosec * 1e-9
                self._lags.append(max(0.0, arr - st))
                self._lags = self._lags[-50:]
                self._lags.sort()
                self._lag = self._lags[len(self._lags) // 2]
                self._odom_seen += 1
                if self._odom_seen == 100:
                    self.get_logger().info(
                        "odom-stamp lag learned: %.3f s (median)" % self._lag)

    def cb(self, msg):
        n = len(msg.ranges)
        r = np.asarray(msg.ranges, dtype=np.float64)
        a = msg.angle_min + msg.angle_increment * np.arange(n)
        x = self.ox + r * np.cos(a)   # laser -> base_footprint translation
        y = self.oy + r * np.sin(a)
        x0, x1, y0, y1 = self.box
        inside = (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1) & np.isfinite(r)
        out = LaserScan()
        out.header = msg.header       # frame_id 'laser' kept; stamp re-based
        now = self.get_clock().now().to_msg()
        stamp_ns = int(now.sec) * 1_000_000_000 + int(now.nanosec) \
            - int(self._lag * 1e9)
        ts = Time()
        ts.sec, ts.nanosec = divmod(stamp_ns, 1_000_000_000)
        out.header.stamp = ts
        out.angle_min = msg.angle_min
        out.angle_max = msg.angle_max
        out.angle_increment = msg.angle_increment
        out.time_increment = msg.time_increment
        out.scan_time = msg.scan_time
        out.range_min = msg.range_min
        out.range_max = msg.range_max
        out.ranges = r.tolist()
        inf = float("inf")
        for i in np.flatnonzero(inside):
            out.ranges[i] = inf
        self.pub.publish(out)
        self._msg_i = getattr(self, "_msg_i", 0) + 1
        if self._msg_i == 1 or self._msg_i % 600 == 0:
            self.get_logger().info(
                "scan re-stamped -%.3f s (learned odom lag)" % self._lag)


def main():
    vals = [float(v) for v in sys.argv[1:5]] if len(sys.argv) >= 5 else \
        [-0.50, 0.30, -0.35, 0.35]
    rclpy.init()
    node = ScanFilter(*vals)
    ex = SingleThreadedExecutor()
    ex.add_node(node)  # explicit executor: plain spin() never fired timers
    try:              # on this unit (proven in webctl_tf_repeater)
        ex.spin()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
