#!/usr/bin/env python3
# generated-from: packtug upstream ca265cc:deploy/nav/webctl_odom_relay.py — FROZEN REFERENCE, not deployed (see README.md)
"""webctl_odom_relay — mirror odom_combined->base_footprint on live /tf.

Why this exists: the vendor base node publishes no odom TF; the robotctl
client builds it from /odom. On this Jetson the C++ TF listeners inside
slam_toolbox / Nav2 do not reliably discover that in-process publisher,
while the Python scan filter on the same /tf stream sees it fine. This
standalone relay is launched by navctl BEFORE the SLAM/Nav2 stack and
re-broadcasts the same transform at 20 Hz from its own node, so the C++
listeners discover it like the laser TF repeater. The cached transform is
republished with a current stamp, so it never disappears while stationary.
"""
import rclpy
from geometry_msgs.msg import TransformStamped
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from tf2_msgs.msg import TFMessage
from tf2_ros import TransformBroadcaster


class OdomRelay(Node):
    def __init__(self):
        super().__init__("webctl_navctl_odom")
        self._b = TransformBroadcaster(self)
        self._tf = None
        self._seen = False
        self.create_subscription(TFMessage, "/tf", self._cb, 100)
        self.create_timer(0.05, self._tick)
        self.get_logger().info(
            "webctl odom relay: mirror odom_combined->base_footprint 20Hz")

    def _cb(self, msg):
        for t in msg.transforms:
            if (t.child_frame_id == "base_footprint"
                    and t.header.frame_id == "odom_combined"):
                self._tf = t.transform
                if not self._seen:
                    self._seen = True
                    self.get_logger().info(
                        "webctl odom relay: first odom TF received")

    def _tick(self):
        if self._tf is None:
            return
        t = TransformStamped()
        t.header.stamp = self.get_clock().now().to_msg()
        t.header.frame_id = "odom_combined"
        t.child_frame_id = "base_footprint"
        t.transform = self._tf
        self._b.sendTransform(t)


def main():
    rclpy.init()
    node = OdomRelay()
    ex = SingleThreadedExecutor()
    ex.add_node(node)
    try:
        ex.spin()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
