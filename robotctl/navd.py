"""navd.py — optional autonomous-navigation helper for the robotctl client
(enable with WEBCTL_NAV=1; with the flag OFF this module is never imported
and client behaviour is byte-identical to v3.2).

It drives the VENDOR wheeltec_ros2 pipeline — slam_toolbox (vendor
mapper_params_online_async.yaml) + wheeltec_nav2 bringup_launch.py
(AMCL + costmaps + waypoint_follower, vendor param_four_wheel_diff_bs.yaml)
via ~/robotctl/navctl.sh (repo-managed). NOTHING under ~/wheeltec_ros2 is
read-write touched.

Design contract (docs/NAV.md):
  * /cmd_vel ownership: while mode == "route" Nav2 owns the topic; the
    client suspends its own 10 Hz teleop publisher while .driving is True.
  * halt(reason) is the one-stop safety exit: cancel the goal + stop the
    whole nav stack + zero mode. Called on latched estop, watchdog trip,
    SIGTERM and explicit nav_off.
  * Every navctl invocation runs in OUR worker thread, NEVER on the ws/net
    thread: blocking that thread starves ping/pong and trips the robot
    watchdog mid-flow.
  * dry-run (integration harness): no subprocesses, scripted pose, full
    state machine + telemetry surface identical.
"""
import base64
import json
import logging
import math
import os
import queue
import re
import subprocess
import threading
import time

log = logging.getLogger("robotctl.navd")

NAV_HOME = os.path.expanduser(os.getenv("WEBCTL_NAV_DIR", "~/robotctl"))
MAPS_DIR = os.path.join(NAV_HOME, "maps")
NAVCTL = os.path.join(NAV_HOME, "navctl.sh")
BASE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
ACTIONS = {"map_on", "map_off", "map_save", "nav_on", "route",
           "nav_cancel", "nav_off", "record", "align", "delete_map"}



class NavDaemon:
    """State machine: off | mapping | localizing | route.

    Client hands us nav payloads from the cmd plane (dedupe on .rid) and
    reads snapshot() at telemetry rate. All ROS access goes through the
    sink's rclpy node (lazy; never built in dry mode)."""

    def __init__(self, sink, post=None, dry=False, robot_id=""):
        self.sink = sink                    # RosSink|Sink|None (.node for rclpy)
        self.post = post                    # client._http bound method
        self.robot_id = robot_id
        self.dry = dry
        self._pose_dbg = 0
        self.lock = threading.Lock()        # guards everything below
        self.mode = "off"
        self.map = ""
        self.error = None
        self.route_total = 0
        self.route_done = 0
        self.record = None                  # last record result {rid,...}
        self.driving = False                # teleop-gate flag (atomic read)
        self.last_rid = 0.0                 # dedupe of handled nav commands
        self._queue = queue.Queue()         # serial worker
        self._worker = threading.Thread(target=self._work_loop, daemon=True,
                                        name="nav-worker")
        self._worker.start()
        self._client = None                 # ActionClient (lazy, real mode)
        self._own_node = None               # navd-dedicated rclpy node
        self._tf_proc = None                # tf2_echo stream (real mode)
        self._tf_started = 0.0              # monotonic ts of current spawn
        self._tf_pose = None                # ((x, y, yaw), monotonic ts)
        self._tf_open_fail = 0.0            # backoff after spawn failure
        self._goal = None
        self._buffer = None                 # tf2 Buffer (lazy)
        self._pose_pub = None               # /initialpose publisher (lazy)
        self._map_timer = 0.0
        self._uploaded = set()              # maps confirmed present on the hub
        self._ip_stop = None                # Event of the running initialpose loop
        self._amcl_rx = 0.0                 # monotonic ts of last /amcl_pose seen
        self._amcl_sub = None               # /amcl_pose listener (created once)
        os.makedirs(MAPS_DIR, exist_ok=True)
        threading.Thread(target=self._resync_mode, daemon=True,
                         name="nav-resync").start()   # non-blocking: it shells out

    # ------------------------------------------------------------- client API
    def handle(self, msg):
        """Accept a nav payload {rid, action, ...}. Net-thread safe: O(1)."""
        try:
            rid = float(msg.get("rid", 0))
            action = msg.get("action")
        except (TypeError, ValueError):
            return
        if action not in ACTIONS:
            return
        with self.lock:
            if rid <= self.last_rid:
                return                      # duplicate / stale replay
            self.last_rid = rid
        self._queue.put(msg)

    def halt(self, reason):
        """Safety exit — callable from ANY thread (estop/watchdog/sigterm).
        The dangerous half (goal cancel + driving=False) happens right now;
        the slow half (kill the stack) is queued to the worker."""
        self._queue.put({"rid": time.time() + 1e-6, "action": "_halt",
                         "reason": reason})

    def snapshot(self):
        """Telemetry view (pose sampled at telemetry rate — 1 Hz)."""
        with self.lock:
            snap = {"mode": self.mode, "map": self.map,
                    "route_total": self.route_total,
                    "route_done": self.route_done,
                    "error": self.error, "record": self.record,
                    "driving": self.driving}
        snap["pose"], snap["frame"] = self._pose_sample()
        snap["local_maps"] = self._local_maps()
        return snap

    # --------------------------------------------------------------- worker
    def _work_loop(self):
        while True:
            msg = self._queue.get()
            try:
                action = msg.get("action")
                if action == "_halt":
                    self._do_halt(msg.get("reason", "halt"))
                else:
                    self._do_action(action, msg)
            except Exception as e:                      # never kill worker
                log.exception("nav action %r failed: %s", msg.get("action"), e)
                with self.lock:
                    self.error = f"{msg.get('action')}: {e}"

    def _do_action(self, action, m):
        if action == "map_on":
            self._navctl(["map-on"], ok_mode="mapping", timeout=300)
        elif action == "map_save":
            name = self._safe(m.get("name"), "room1")
            if self._navctl(["map-save", name], ok_mode=None, timeout=120):
                self._upload_map(name)
        elif action == "map_off":
            self._navctl(["stop"], ok_mode="off")
        elif action == "nav_on":
            name = self._safe(m.get("map"), "")
            if not name:
                with self.lock:
                    self.error = "nav_on: map name required"
                return
            if self._navctl(["stack-on", name], ok_mode="localizing", timeout=180):
                with self.lock:
                    self.map = name
                # AMCL starts assuming the robot sits at map origin — which
                # is only true if it never moved since saving. Hand it a
                # pose anyway (default 0,0,0) and RE-publish until amcl_pose
                # confirms: one-shot initialpose is lost during AMCL's
                # startup window (nav2_system_tests does the same loop).
                self._republish_initial_pose(m.get("align_pose") or
                                             [0.0, 0.0, 0.0])
        elif action == "align":
            point = m.get("align_pose")
            if point:
                self._republish_initial_pose(point)
        elif action == "route":
            self._start_route(m.get("poses") or [], m)
        elif action == "nav_cancel":
            self._cancel_goal()
        elif action == "nav_off":
            self._do_halt("nav_off")
        elif action == "record":
            self._do_record(m)
        elif action == "delete_map":
            name = self._safe(m.get("map"), "")
            if name:
                for ext in (".pgm", ".yaml"):
                    try:
                        os.remove(os.path.join(MAPS_DIR, name + ext))
                    except OSError:
                        pass
                self._uploaded.discard(name)
                log.info("nav: deleted local map '%s'", name)

    # ------------------------------------------------------- action internals
    def _start_route(self, poses, m=None):
        """poses: [[x, y, yaw], …] in the MAP frame. THE only motion path."""
        if len(poses) < 1:
            with self.lock:
                self.error = "route: empty pose list"
            return
        with self.lock:
            not_up = self.mode not in ("localizing", "route")
        if not_up and not self._auto_arm(m or {}):
            return
        with self.lock:
            self.route_total = len(poses)
            self.route_done = 0
            self.error = None
        if self.dry:
            with self.lock:
                self.mode, self.driving = "route", True
            log.info("nav(dry): route %d waypoints (simulated)", len(poses))
            threading.Timer(2.0, self._dry_route_done,
                            args=(len(poses),)).start()
            return
        if not self._await_pose(20.0):
            with self.lock:
                self.error = ("route: not localized yet — AMCL has no pose "
                              "(press ⌖ align on the robot's true spot)")
            return
        try:
            client = self._action_client()
            if client is None or not client.wait_for_server(timeout_sec=5.0):
                with self.lock:
                    self.error = "route: /follow_waypoints server not up"
                return
            goal = self._build_goal(poses)
            with self.lock:
                self.mode, self.driving = "route", True
            fut = client.send_goal_async(
                goal, feedback_callback=self._feedback_cb)
            fut.add_done_callback(self._goal_cb)
            log.info("nav: route goal sent (%d waypoints)", len(poses))
        except Exception as e:
            with self.lock:
                self.error, self.driving = f"route: {e}", False
            log.exception("route start failed")

    def _dry_route_done(self, n):
        with self.lock:
            self.route_done = n
            self.mode, self.driving = "localizing", False
        log.info("nav(dry): route complete")

    def _auto_arm(self, m):
        """START ROUTE with the stack DOWN: bring Nav2 up on the best-known
        map and arm AMCL, then let the route proceed — the hub UI treats the
        route button as 'do what is needed to drive this'. Refuses (with a
        surfaced error) only when no single obvious map exists. Rationale:
        v3.5.2 incident — route silently refused in mode 'off' and the user
        saw 'robot has no action'."""
        name = self._safe(m.get("map"), "") or self.map
        if not name:
            maps = self._local_maps()
            if len(maps) == 1:
                name = maps[0]
            elif maps:
                with self.lock:
                    self.error = ("route: several maps saved — press 🎯 nav "
                                  "on the one you want first")
                return False
        if not name:
            with self.lock:
                self.error = "route: no saved map on the robot — make one first (🗺)"
            return False
        log.info("nav: auto-arming stack on '%s' for route", name)
        if not self._navctl(["stack-on", name], ok_mode="localizing",
                            timeout=180):
            return False
        with self.lock:
            self.map = name
        self._republish_initial_pose(m.get("align_pose") or [0.0, 0.0, 0.0])
        return True

    def _await_pose(self, timeout=20.0):
        """Block (worker thread) until the TF map->base pose is live or the
        deadline passes. A route goal fired while AMCL has no pose would be
        accepted and then wander blind in the costmap frame."""
        if self.dry:
            return True
        end = time.monotonic() + timeout
        t0 = time.monotonic()
        while time.monotonic() < end:
            pose, _frame = self._pose_sample()
            if pose is not None:
                if time.monotonic() - t0 > 1.0:
                    log.info("nav: pose acquired after %.1fs", time.monotonic() - t0)
                return True
            time.sleep(0.5)
        log.warning("nav: no pose after %.0fs — refusing route", timeout)
        return False

    def _goal_cb(self, fut):
        try:
            goal = fut.result()
            self._goal = goal
            if goal is None or not goal.accepted:
                with self.lock:
                    self.error = "route: goal rejected"
                    self.mode, self.driving = "localizing", False
                return
            goal.result_future.add_done_callback(self._result_cb)
        except Exception as e:
            with self.lock:
                self.error, self.driving, self.mode = f"goal: {e}", False, "localizing"

    def _result_cb(self, fut):
        try:
            res = fut.result()
            missed = len(getattr(res, "missed_waypoints", []) or [])
            msg = None if not missed else f"{missed} waypoint(s) missed"
        except Exception as e:
            msg = f"route result error: {e}"
        with self.lock:
            self.mode, self.driving = "localizing", False
            if msg:
                self.error = msg
        log.info("nav: route ended (%s)", msg or "ok")

    def _feedback_cb(self, fb):
        try:
            done = int(fb.feedback.current_waypoint)
        except Exception:
            return
        with self.lock:
            self.route_done = min(done, self.route_total)

    def _cancel_goal(self):
        goal = self._goal
        if goal is not None:
            try:
                goal.cancel_goal_async()
            except Exception as e:
                log.warning("cancel failed: %s", e)
        with self.lock:
            self.driving = False
            if self.mode == "route":
                self.mode = "localizing"

    def _do_halt(self, reason):
        # Soft reasons (e-stop pressed, transient link loss, client restart)
        # must NOT tear the SLAM/EKF stack down — a 15s WiFi flap or a client
        # upgrade used to destroy minutes of mapping, and setsid'd processes +
        # startup resync exist precisely so the plane outlives the client.
        # Only the explicit nav-off button tears the stack down.
        soft = reason != "nav_off"
        with self.lock:
            self.driving = False
            self.error = f"halted: {reason}"
            if soft and self.mode == "route":
                self.mode = "localizing"   # stack still up; route aborted
        goal = self._goal
        if goal is not None:
            try:
                goal.cancel_goal_async()
            except Exception:
                pass
        if not soft and not self.dry and (self.mode != "off" or self._nav_running()):
            self._navctl(["stop"], ok_mode="off", timeout=20)
        elif not soft:
            with self.lock:
                self.mode, self.map = "off", self.map
        log.warning("nav HALT (%s, %s)", reason, "soft" if soft else "hard")

    def _do_record(self, m):
        name = self._safe(m.get("name"), f"wp-{int(time.time())}")
        rid = m.get("point_rid")
        if self.dry:
            x, y, yaw, frame = 0.5, -0.5, 1.57, "map"
        else:
            pose, frame = self._pose_sample()
            if pose is None:
                with self.lock:
                    self.error = "record: no pose yet (is nav/slam running?)"
                return
            x, y, yaw = pose
        with self.lock:
            self.record = {"rid": rid, "name": name, "x": round(x, 4),
                           "y": round(y, 4), "yaw": round(yaw, 4),
                           "frame": frame}
        log.info("nav: recorded waypoint %s (%.2f, %.2f, %.1f deg) frame=%s",
                 name, x, y, math.degrees(yaw), frame)

    # ---------------------------------------------------------- ROS plumbing
    def _node(self):
        """navd's OWN rclpy node, spun by its own executor thread.

        v3.4.4: this used to borrow the sink's node — but a
        TransformListener attached there never saw map/odom TF in the
        client process (probe: can_transform=0 even with 1s timeout,
        while an identical standalone listener got pose instantly —
        humble SingleThreadedExecutor shared with sink callbacks starves
        TF delivery). Own node, own executor, no hitchhiking."""
        if self.dry:
            return None
        if self._own_node is None:
            import rclpy
            import threading
            from rclpy.executors import SingleThreadedExecutor
            node = rclpy.create_node("webctl_navd")
            ex = SingleThreadedExecutor()
            ex.add_node(node)
            threading.Thread(target=ex.spin, daemon=True,
                             name="webctl-navd-spin").start()
            self._own_node = node
        return self._own_node

    def _action_client(self):
        if self._client is not None:
            return self._client
        node = self._node()
        if node is None:
            return None
        from rclpy.action import ActionClient
        from nav2_msgs.action import FollowWaypoints
        self._client = ActionClient(node, FollowWaypoints, "/follow_waypoints")
        return self._client

    def _build_goal(self, poses):
        from nav2_msgs.action import FollowWaypoints
        goal = FollowWaypoints.Goal()
        stamps = []
        for p in poses:
            x, y, yaw = float(p[0]), float(p[1]), float(p[2])
            ps = self._pose_stamped("map", x, y, yaw)
            stamps.append(ps)
        goal.poses = stamps
        return goal

    def _pose_stamped(self, frame, x, y, yaw):
        from geometry_msgs.msg import PoseStamped
        ps = PoseStamped()
        ps.header.frame_id = frame
        node = self._node()
        if node is not None:
            ps.header.stamp = node.get_clock().now().to_msg()
        ps.pose.position.x, ps.pose.position.y = x, y
        ps.pose.orientation.z = math.sin(yaw / 2.0)
        ps.pose.orientation.w = math.cos(yaw / 2.0)
        return ps

    def _republish_initial_pose(self, point):
        """AMCL initialpose, nav2's own retry pattern (web-grounded 2026-11-11:
        nav2 PR #774 + nav_to_pose_tester_node.py — AMCL's /initialpose
        subscriber is default/volatile QoS, so publishing while AMCL is still
        activating silently loses it; the official tester re-publishes until
        amcl_pose comes back). transient_local pub adds a second safety net.
        """
        if self.dry:
            log.info("nav(dry): initialpose (republish) %s", point)
            return
        node = self._node()
        if node is None:
            return
        try:
            from geometry_msgs.msg import PoseWithCovarianceStamped
        except Exception as e:
            log.warning("initialpose import failed: %s", e)
            return
        if self._ip_stop is not None:
            self._ip_stop.set()               # cancel a previous align loop
        stop = threading.Event()
        self._ip_stop = stop
        threading.Thread(target=self._initialpose_loop,
                         args=(node, [float(v) for v in point[:3]], stop,
                               PoseWithCovarianceStamped),
                         daemon=True, name="nav-initialpose").start()

    def _amcl_pose_seen(self, _msg=None):
        self._amcl_rx = time.monotonic()

    def _initialpose_loop(self, node, pt, stop, Msg):
        # Plain default-QoS publishers, like nav2's own tester node —
        # rclpy QoS enums are a known import landmine on this robot
        # (shim check "QoSProfile built with depth=1 and NO durability/
        # reliability enums"); the republish loop alone covers the race.
        try:
            if self._pose_pub is None:
                self._pose_pub = node.create_publisher(Msg, "/initialpose", 10)
            if self._amcl_sub is None:
                self._amcl_sub = node.create_subscription(
                    Msg, "/amcl_pose", self._amcl_pose_seen, 10)
        except Exception as e:
            log.warning("initialpose setup failed: %s", e)
            return
        msg = Msg()
        msg.header.frame_id = "map"
        x, y, yaw = pt[0], pt[1], pt[2]
        msg.pose.pose.position.x, msg.pose.pose.position.y = x, y
        msg.pose.pose.orientation.z = math.sin(yaw / 2.0)
        msg.pose.pose.orientation.w = math.cos(yaw / 2.0)
        cov = msg.pose.covariance
        cov[0] = cov[7] = 0.25               # 0.5 m sigma x,y
        cov[35] = 0.1                        # ~0.3 rad sigma yaw
        t0 = time.monotonic()
        while not stop.wait(1.2) and time.monotonic() - t0 < 15.0:
            msg.header.stamp = node.get_clock().now().to_msg()
            try:
                self._pose_pub.publish(msg)
            except Exception:
                pass
            if self._amcl_rx >= t0:
                log.info("nav: initialpose accepted (amcl_pose seen)")
                return
        log.warning("initialpose: no amcl_pose confirmation within 15 s — "
                    "press ⌖ on the waypoint the robot physically sits on")

    def _local_maps(self):
        """Names of map artifacts saved on THIS robot (navctl map-save)."""
        try:
            names = sorted({f.rsplit(".", 1)[0]
                            for f in os.listdir(MAPS_DIR)
                            if f.endswith((".pgm", ".yaml"))})
        except OSError:
            return []
        return names[:12]

    def _heal_uploads(self):
        """Re-send every local map the hub may never have received: a flaky
        link drops uploads silently, and an empty hub list makes the 🎯 nav
        button permanently disabled (corridor incident, 2026-11-11)."""
        if self.dry or self.post is None:
            return
        time.sleep(3.0)                       # let the client session warm up
        for name in self._local_maps():
            if name in self._uploaded:
                continue
            log.info("nav: healing map upload '%s'", name)
            self._upload_map(name)

    # ------------------------------------------------------------------ pose
    # v3.4.4 evidence: on this Jetson, tf2_ros PYTHON listeners (both a
    # dedicated node AND a standalone probe process) receive the camera TF
    # frames but NEVER slam/EKF map/odom frames whenever the client process
    # is alive — rclcpp tooling (tf2_echo) sees everything. DDS/SHM quirk,
    # root cause beyond our pay grade. So the pose rides a persistent
    # rclcpp tf2_echo STREAM parsed into a cache; one process, zero-per-
    # sample, self-healing.

    def _tf_stream(self):
        """Ensure the tf2_echo map->base_footprint stream is ALIVE AND TALKING."""
        if self._tf_proc is not None:
            if self._tf_proc.poll() is None:
                # A tf2_echo spawned before the TF tree existed logs one
                # complaint then goes SILENT FOREVER (tf2_echo behavior).
                # Recycle streams that stopped producing pose samples.
                seen = self._tf_pose[1] if self._tf_pose else self._tf_started
                if time.monotonic() - seen > 120.0:
                    log.warning("tf stream silent >120s — recycling for respawn")
                    # killpg: terminate() only reaped the `ros2 run` wrapper;
                    # the real tf2_echo binary orphaned out (23 zombies in
                    # one afternoon, DDS spam -> load 14). start_new_session
                    # made proc.pid the pgid, so kill the whole group.
                    try:
                        os.killpg(os.getpgid(self._tf_proc.pid), 9)
                    except OSError:
                        try:
                            self._tf_proc.kill()
                        except OSError:
                            pass
                    self._tf_proc = None
                else:
                    return
            else:
                self._tf_proc = None  # died (TF tree went down) -> respawn
        if time.monotonic() - self._tf_open_fail < 30.0:
            return  # back off after a failed spawn; slam may not exist yet
        ros = os.environ.get("WEBCTL_ROS_SETUP", "/opt/ros/humble/setup.bash")
        try:
            proc = subprocess.Popen(
                ["bash", "-lc",
                 f"source {ros} && exec ros2 run tf2_ros tf2_echo "
                 "map base_footprint"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1, start_new_session=True)
        except OSError as e:
            self._tf_open_fail = time.monotonic()
            log.warning("tf stream spawn failed: %s", e)
            return
        self._tf_proc = proc
        self._tf_started = time.monotonic()
        threading.Thread(target=self._tf_reader, args=(proc,),
                         daemon=True, name="webctl-navd-tf").start()

    def _tf_reader(self, proc):
        import re
        trans = re.compile(r"- Translation: \[([^]]*)]")
        quat = re.compile(r"- Rotation: in Quaternion \[([^]]*)]")
        x = y = 0.0
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                m = trans.search(line)
                if m:
                    v = [float(s) for s in m.group(1).split(",")]
                    x, y = v[0], v[1]
                    continue
                m = quat.search(line)
                if m:
                    qx, qy, qz, qw = (float(s) for s in m.group(1).split(","))
                    yaw = math.atan2(2.0 * (qw * qz + qx * qy),
                                     1.0 - 2.0 * (qy * qy + qz * qz))
                    self._tf_pose = ((x, y, yaw), time.monotonic())
        except (ValueError, OSError):
            pass

    def _pose_sample(self):
        """(x, y, yaw) + frame from the TF stream cache (map frame)."""
        if self.dry:
            t = time.monotonic() * 0.1
            return ((0.5 * math.cos(t), 0.5 * math.sin(t), t % 6.2831853),
                    "map") if self.mode != "off" else (None, None)
        self._tf_stream()
        cached = self._tf_pose
        if cached and time.monotonic() - cached[1] < 4.0:
            return cached[0], "map"
        return None, None

    # ------------------------------------------------------- navctl wrapper
    def _navctl(self, args, ok_mode=None, timeout=60):
        """Run navctl.sh; on success apply ok_mode (if given). Returns bool."""
        if self.dry:
            log.info("nav(dry): navctl %s", " ".join(args))
            mode = {"map-on": "mapping", "stop": "off",
                    "stack-on": "localizing"}.get(args[0], self.mode)
            with self.lock:
                self.mode = ok_mode if ok_mode is not None else mode
                if args and args[0] == "stop":
                    self.driving = False
            if args and args[0] == "map-save" and len(args) > 1:
                self._upload_map(args[1], dry=True)
            return True
        try:
            # Capture via TEMP FILES, never PIPEs: navctl.sh's `ros2` calls
            # auto-start the ros2 daemon, which inherits any pipe we pass —
            # the pipe then never EOFs and run() blocks to its timeout even
            # though the stack came up (v3.4.7 incident, 2026-11-11).
            import tempfile
            with tempfile.TemporaryFile("w+") as _o, \
                    tempfile.TemporaryFile("w+") as _e:
                r = subprocess.run(["bash", NAVCTL] + list(args),
                                   stdout=_o, stderr=_e, text=True,
                                   stdin=subprocess.DEVNULL, timeout=timeout)
                _o.seek(0); _e.seek(0)
                r.stdout, r.stderr = _o.read(), _e.read()
        except (OSError, subprocess.TimeoutExpired) as e:
            with self.lock:
                self.error = f"navctl {' '.join(args)}: {e}"
            return False
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            with self.lock:
                self.error = f"navctl {args[0]}: {out[-240:]}"
            log.warning("navctl %s failed: %s", args, out[-240:])
            return False
        with self.lock:
            self.error = None  # a success clears any stale navctl error
        if ok_mode is not None:
            with self.lock:
                self.mode = ok_mode
                if ok_mode == "off":
                    self.driving = False
        log.info("navctl %s -> %s", " ".join(args), out[-160:] or "ok")
        return True

    def _resync_mode(self):
        """Adopt the truth about the nav plane from the robot itself —
        a client restart must not report 'idle' while SLAM/Nav2 run on
        (they're setsid'd and outlive the client by design)."""
        if self.dry:
            return
        try:
            r = subprocess.run(["bash", NAVCTL, "status"],
                               capture_output=True, text=True, timeout=15)
            st = (r.stdout or "").strip()
            if "localizing" in st:
                self.mode = "localizing"
            elif "mapping" in st:
                self.mode = "mapping"
            if self.mode != "off":
                log.info("nav resync: adopting mode=%s from robot", self.mode)
        except Exception as e:
            log.debug("nav resync failed: %s", e)
        try:
            self._heal_uploads()
        except Exception:
            log.debug("map heal failed", exc_info=True)

    def _nav_running(self):
        if self.dry:
            return False
        try:
            r = subprocess.run(["bash", NAVCTL, "status"],
                               capture_output=True, text=True, timeout=10)
            return "running" in (r.stdout or "")
        except Exception:
            return False

    # ------------------------------------------------------------ map upload
    def _upload_map(self, name, dry=False):
        if dry:
            self._uploaded.add(name)
            return True
        try:
            pgm = open(os.path.join(MAPS_DIR, f"{name}.pgm"), "rb").read()
            yml = open(os.path.join(MAPS_DIR, f"{name}.yaml"), "rb").read()
        except OSError as e:
            with self.lock:
                self.error = f"map upload: {e}"
            return False
        if len(pgm) > 8 * 1024 * 1024:
            with self.lock:
                self.error = "map upload: pgm > 8 MB"
            return False
        if self.post is None:
            with self.lock:
                self.error = "map upload: no http post available"
            return False
        # gzip first (web-grounded choice: PGMs are run-length friendly, a
        # multi-MB map collapses to a few hundred KB — one POST surviving
        # the flaky warehouse link beats three fat ones), then retry x3.
        import zlib
        gz = zlib.compress(pgm, 6)
        body = json.dumps({"name": name, "enc": "gz",
                           "pgm_gz_b64": base64.b64encode(gz).decode(),
                           "yaml": yml.decode("utf-8", "replace")}).encode()
        delay = 2.0
        for attempt in (1, 2, 3):
            try:
                code, resp = self.post(
                    "POST", f"/robotctl/{self.robot_id}/map", body=body,
                    headers={"Content-Type": "application/json"},
                    timeout=30.0)
            except Exception as e:
                code, resp = -1, repr(e)
            if code == 200:
                self._uploaded.add(name)
                with self.lock:
                    if self.error and self.error.startswith("map upload"):
                        self.error = None
                log.info("nav: map '%s' uploaded (%d B pgm -> %d B body)",
                         name, len(pgm), len(body))
                return True
            log.warning("map upload attempt %d/3 for '%s': http %s %r",
                        attempt, name, code, str(resp)[:160])
            with self.lock:
                self.error = (f"map upload '{name}' x{attempt}: "
                              f"http {code}: {str(resp)[:160]!r}")
            if attempt < 3:
                time.sleep(delay)
                delay *= 2
        return False

    # ---------------------------------------------------------------- utils
    @staticmethod
    def _safe(name, default):
        if isinstance(name, str) and BASE_NAME.fullmatch(name):
            return name
        return default
