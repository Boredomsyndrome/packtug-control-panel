"""DRF serializers for the wheeltec-webctl API.

Input validation (VelocityCommandSerializer) is the first safety layer;
the hub clamps again to MAX_LIN/MAX_ROT regardless of what passes here.
TelemetrySerializer guards the robot uplink (numbers only, no NaN/inf).
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
    """Response for /api/robot/<id>/cmd — the actually-applied (clamped) values."""
    ok = serializers.BooleanField()
    vx = serializers.FloatField()
    wz = serializers.FloatField()
    clamped = serializers.BooleanField()


class RobotStatusSerializer(serializers.Serializer):
    """Read-only representation of RobotHub.status()."""
    id = serializers.CharField()
    online = serializers.BooleanField()
    battery_v = serializers.FloatField(allow_null=True)
    battery_age = serializers.FloatField(allow_null=True)
    cmd = VelocityCommandSerializer(read_only=True)
    cmd_age = serializers.FloatField(allow_null=True)
    applied = VelocityCommandSerializer(read_only=True)
    estop = serializers.BooleanField()
    watchdog_trips = serializers.IntegerField()
    camera = serializers.BooleanField()
    transport = serializers.CharField(allow_null=True, required=False)
    ws_connected = serializers.BooleanField(required=False)
    cmd_timeout = serializers.FloatField()
    max_lin = serializers.FloatField()
    max_rot = serializers.FloatField()
    uptime = serializers.FloatField()
    seq = serializers.IntegerField()


class TelemetrySerializer(serializers.Serializer):
    """Uplink body for POST /robotctl/<id>/telemetry (robot client -> server)."""
    battery_v = serializers.FloatField(required=False, allow_null=True,
                                       min_value=0.0, max_value=100.0)
    applied_vx = serializers.FloatField(required=False, default=0.0,
                                        min_value=-1e3, max_value=1e3)
    applied_wz = serializers.FloatField(required=False, default=0.0,
                                        min_value=-1e3, max_value=1e3)
    watchdog_trips = serializers.IntegerField(required=False, default=0,
                                              min_value=0, max_value=10**9)

    def validate(self, attrs):
        for value in attrs.values():
            if isinstance(value, float) and not math.isfinite(value):
                raise serializers.ValidationError("values must be finite")
        return attrs
