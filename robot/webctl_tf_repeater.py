#!/usr/bin/env python3
"""webctl_tf_repeater — continuous base_footprint->laser transform, humble-safe.

WHY THIS EXISTS (v3.6.5, the 'queue is full' saga finale):
humble's static_transform_publisher latches /tf_static ONCE. On this loaded
Jetson, slam_toolbox's TRANSIENT_LOCAL /tf_static listener (late-joining,
same DDS latch-delivery quirk that starved the v3.4.4 pose listeners) NEVER
receives the latch, so its TF buffer never knows frame 'laser', message_
filters drops 100% of scans ("frame 'laser' ... queue is full"), the map
frame never comes into existence, and nav pose stays None forever.
`--period` (continuous re-publish) is iron+/rolling only — humble exits 1.
Live dynamic /tf streaming, however, DEMONSTRABLY works here (tf2_echo
always caught the transform). So: publish the same static transform as a
regular geometry_msgs/TransformStamped on /tf at 5 Hz forever.

Values are the exact vendor launch offsets (x=0.157, z=0.257), identity
rotation — identical to what the old static publisher sent.

Run under navctl.sh (ensure_tf) as __node:=webctl_navctl_tf so the existing
stop/proc guards keep matching.
"""
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import TransformStamped
from tf2_ros import TransformBroadcaster


class TfRepeater(Node):
    def __init__(self):
        super().__init__("webctl_navctl_tf")
        self._b = TransformBroadcaster(self)
        self.timer = self.create_timer(0.04, self._pub)  # 25Hz floor
        # THE race-killer (v3.6.5): slam's message_filter needs a TF for the
        # EXACT scan stamp; any period coarser than the 12 Hz scan guarantees
        # every scan waits up to a full period -> filter queue(10) overflows
        # -> 'queue is full' on 100% of scans (this was true at 5 Hz — the
        # original sin of the repeater, independent of message size). Mirror
        # every scan with a laser TF carrying that scan's own header stamp.
        from sensor_msgs.msg import LaserScan
        from rclpy.qos import qos_profile_sensor_data
        # mirror the FILTERED stream: its stamps are re-based to system
        # time by webctl_scan_filter, so mirrored TFs are exact-time
        # matches for slam's message_filter on the same clock.
        self.create_subscription(LaserScan, "/scan_filtered", self._pub,
                                 qos_profile_sensor_data)
        self._n = 0
        self.get_logger().info("webctl tf repeater: base_footprint->laser 25Hz + scan-mirrored")

    def _pub(self, msg=None):
        t = TransformStamped()
        # v3.6.5 RACE FIX: mirroring with the SCAN's header stamp let old
        # mirrors age out of slam's tf buffer (tf_buffer_duration 30 s)
        # before its filter retried the lookup -> 'extrapolation into the
        # past' -> failure-cache overflow -> 'queue is full'. Buffer keys
        # by RECEIPT time (tf2 semantics), so stamping receipt-now makes
        # every mirrored static instantly valid forever (huge buffer dur
        # below); scans are lag-stamped by the filter, so lookups land
        # inside brackets — never extrapolation.
        t.header.stamp = self.get_clock().now().to_msg()
        t.header.frame_id = "base_footprint"
        t.child_frame_id = "laser"
        t.transform.translation.x = 0.157
        t.transform.translation.y = 0.0
        t.transform.translation.z = 0.257
        t.transform.rotation.w = 1.0
        self._b.sendTransform(t)
        self._n += 1
        if self._n == 5 or self._n % 150 == 0:  # loud proof, then hourly-ish
            self.get_logger().info("sent %d laser transforms" % self._n)


def main():
    rclpy.init()
    node = TfRepeater()
    from rclpy.executors import SingleThreadedExecutor
    ex = SingleThreadedExecutor()
    ex.add_node(node)  # v3.6.5: plain spin() never fired the timer here (node
    try:              # logged boot line but /tf saw ZERO frames); explicit
        ex.spin()     # executor fixed it, same trick as the cam/sink nodes.
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
