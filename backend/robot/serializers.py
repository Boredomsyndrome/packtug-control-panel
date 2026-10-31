"""DRF serializers for the wheeltec-webctl API.

Input validation (VelocityCommandSerializer) is the first safety layer;
the bridge clamps again to MAX_LIN/MAX_ROT regardless of what passes here.
"""
import math

from rest_framework import serializers


class VelocityCommandSerializer(serializers.Serializer):
    """POST body for a velocity command: {"vx": m/s, "wz": rad/s}.

    Also reused read-only for echoing nested {"vx", "wz"} in responses/status.
    """
    vx = serializers.FloatField(required=False, default=0.0,
                                min_value=-1e6, max_value=1e6)
    wz = serializers.FloatField(required=False, default=0.0,
                                min_value=-1e6, max_value=1e6)

    def validate(self, attrs):
        for name in ("vx", "wz"):
            value = attrs.get(name, 0.0)
            if not math.isfinite(value):
                raise serializers.ValidationError({name: "must be a finite number"})
        return attrs


class CommandResultSerializer(serializers.Serializer):
    """Response for /api/robot/cmd — the actually-applied (clamped) values."""
    ok = serializers.BooleanField()
    vx = serializers.FloatField()
    wz = serializers.FloatField()
    clamped = serializers.BooleanField()


class RobotStatusSerializer(serializers.Serializer):
    """Read-only representation of robot.bridge.status()."""
    connected = serializers.BooleanField()
    owns_bridge = serializers.BooleanField()
    ws_url = serializers.CharField()
    battery_v = serializers.FloatField(allow_null=True)
    battery_topic = serializers.CharField(allow_null=True)
    battery_age = serializers.FloatField(allow_null=True)
    cmd = VelocityCommandSerializer(read_only=True)
    cmd_age = serializers.FloatField(allow_null=True)
    estop = serializers.BooleanField()
    cmd_timeout = serializers.FloatField()
    uptime = serializers.FloatField()
    publishes = serializers.IntegerField()
    last_error = serializers.CharField(allow_null=True)
    max_lin = serializers.FloatField()
    max_rot = serializers.FloatField()
