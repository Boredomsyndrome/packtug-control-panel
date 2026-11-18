/**
 * Reserved integration surface.
 *
 * The prototype runs entirely on mock data, but every value that must later
 * come from the Django backend or the WHEELTEC S200 / ROS 2 stack is listed
 * here so the wiring point is explicit and stable.
 *
 * ── REST (Django REST Framework, PostgreSQL) ───────────────────────────────
 *   GET    /api/health                     -> { status, build, time }
 *   GET    /api/agvs                       -> Agv[]
 *   GET    /api/agvs/:id                   -> Agv
 *   GET    /api/cages                      -> Cage[]
 *   GET    /api/zones                      -> Zone[]
 *   GET    /api/orders                     -> Order[]
 *   POST   /api/orders                     -> Order            (create)
 *   GET    /api/orders/:id                 -> Order
 *   POST   /api/orders/:id/pack            -> PackingPlan      (server bin-packing)
 *   POST   /api/orders/:id/dispatch        -> Mission          (assign AGV)
 *   GET    /api/missions                   -> Mission[]
 *   POST   /api/missions/:id/control       -> Mission          (pause|resume|abort)
 *   POST   /api/zones/:id/reoptimise       -> { proposedUtil } (re-plan zone)
 *   GET    /api/metrics/throughput         -> ThroughputPoint[]
 *   POST   /api/auth/login                 -> { token, user }
 *
 * ── Arm bridge (Niryo Ned1, `server/arm_server.py`) ─────────────────────────
 *   GET    /api/arm/status                 -> ArmStatus (cached + live refresh)
 *   POST   /api/arm/connect                -> ArmStatus  { ip }
 *   POST   /api/arm/disconnect             -> ArmStatus
 *   POST   /api/arm/jog                    -> ArmStatus  { joint: 1..6, delta_deg }
 *   POST   /api/arm/joint                  -> ArmStatus  { joint: 1..6, angle_deg }
 *   POST   /api/arm/joints                 -> ArmStatus  { joints_deg: number[6] }
 *   POST   /api/arm/learning               -> ArmStatus  { enabled, confirm }
 *   POST   /api/arm/velocity               -> ArmStatus  { percent: 1..20 }
 *   POST   /api/arm/reach                  -> ArmStatus  { max_reach_m: 0.15..0.50 }
 *   POST   /api/arm/motors/enable          -> ArmStatus  (leave learning mode)
 *   POST   /api/arm/gripper/open           -> ArmStatus
 *   POST   /api/arm/gripper/close          -> ArmStatus
 *   POST   /api/arm/tool/scan              -> ArmStatus  (detect attached tool)
 *   POST   /api/arm/home                   -> ArmStatus  (fold to safe home pose)
 *   POST   /api/arm/recover                -> ArmStatus  (slow retract after collision/fault)
 *   POST   /api/arm/fault/clear            -> ArmStatus  (acknowledge after inspection)
 *   POST   /api/arm/calibrate              -> ArmStatus  { confirm: true } (auto sweep)
 *   GET    /api/camera/stream              -> MJPEG proxy from the Jetson
 *   POST   /api/vision/qr                  -> QR detection in a camera ROI
 *   GET    /api/vehicle                    -> live pull-mode AGV status
 *   GET    /api/arm/poses                  -> { poses: ArmPosePreset[] }
 *   POST   /api/arm/poses                  -> { pose, poses }  { name, joints_deg? }
 *   DELETE /api/arm/poses/:name            -> { poses }
 *   POST   /api/arm/poses/:name/apply      -> ArmStatus  { linear: bool } (recall pose)
 *   GET    /api/arm/sequences              -> { sequences: ArmSequence[] }
 *   POST   /api/arm/sequences              -> { sequence, sequences }  { name, poses[], dwell_sec }
 *   DELETE /api/arm/sequences/:name        -> { sequences }
 *
 * ── WebSocket telemetry ────────────────────────────────────────────────────
 *   WS     /ws/telemetry   server -> client
 *          { type: 'agv',     payload: Agv }
 *          { type: 'cage',    payload: Cage }
 *          { type: 'mission', payload: Mission }
 *          { type: 'metrics', payload: ThroughputPoint }
 *
 * ── ROS 2 bridge (Jetson Orin <-> backend) ─────────────────────────────────
 *   Subscribes : /odom, /battery_state, /imu/data_raw, /scan, /wheeltec/*
 *   Publishes  : /cmd_vel, /mission/command, /mission/feedback
 *   Robot s/w  : wheeltec_ros2 / turn_on_wheeltec_robot launch stack
 *
 * Set VITE_API_BASE and VITE_MOCK=false in .env to switch off mock mode for
 * the panel business APIs. Arm/camera/vehicle endpoints are always live.
 */

export const API_BASE = import.meta.env.VITE_API_BASE ?? '/api'
export const MOCK_MODE = (import.meta.env.VITE_MOCK ?? 'true') !== 'false'

export interface EndpointSpec {
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'WS'
  path: string
  description: string
  status: 'implemented-mock' | 'implemented-live' | 'planned'
}

export const ENDPOINTS: EndpointSpec[] = [
  { method: 'GET', path: '/api/agvs', description: 'Fleet state: pose, battery, status, mission', status: 'implemented-mock' },
  { method: 'GET', path: '/api/cages', description: 'Roll-cage fill level, weight, centre of gravity', status: 'implemented-mock' },
  { method: 'GET', path: '/api/zones', description: 'Static storage zones and temperatures', status: 'implemented-mock' },
  { method: 'GET', path: '/api/orders', description: 'Order list and status', status: 'implemented-mock' },
  { method: 'POST', path: '/api/orders', description: 'Create a picking order', status: 'implemented-mock' },
  { method: 'POST', path: '/api/orders/:id/pack', description: 'Run server-side 3D bin packing', status: 'planned' },
  { method: 'POST', path: '/api/orders/:id/dispatch', description: 'Assign order to an AGV', status: 'implemented-mock' },
  { method: 'GET', path: '/api/missions', description: 'Mission queue and progress', status: 'implemented-mock' },
  { method: 'POST', path: '/api/missions/:id/control', description: 'pause / resume / abort a mission', status: 'implemented-mock' },
  { method: 'POST', path: '/api/zones/:id/reoptimise', description: 'Re-optimise packing of a zone', status: 'implemented-mock' },
  { method: 'GET', path: '/api/metrics/throughput', description: 'Historical throughput and utilisation', status: 'implemented-mock' },
  { method: 'WS', path: '/ws/telemetry', description: 'Live AGV / cage / mission telemetry stream', status: 'planned' },
  { method: 'POST', path: '/api/auth/login', description: 'RBAC login', status: 'implemented-mock' },
  { method: 'GET', path: '/api/arm/status', description: 'Ned1 joints, pose, calibration and speed', status: 'implemented-live' },
  { method: 'POST', path: '/api/arm/connect', description: 'Open a PyNiryo TCP session to the Ned1', status: 'implemented-live' },
  { method: 'POST', path: '/api/arm/disconnect', description: 'Close the Ned1 session', status: 'implemented-live' },
  { method: 'POST', path: '/api/arm/jog', description: 'Nudge one joint by ≤60° at the current speed cap', status: 'implemented-live' },
  { method: 'POST', path: '/api/arm/joint', description: 'Move one joint to a dragged absolute angle (≤60°)', status: 'implemented-live' },
  { method: 'POST', path: '/api/arm/joints', description: 'Move all joints to absolute targets (≤60° each)', status: 'implemented-live' },
  { method: 'POST', path: '/api/arm/learning', description: 'Release motors for manual posing / re-engage', status: 'implemented-live' },
  { method: 'POST', path: '/api/arm/velocity', description: 'Set the arm speed cap (1–20%)', status: 'implemented-live' },
  { method: 'POST', path: '/api/arm/reach', description: 'Set the max horizontal gripper-tip reach', status: 'implemented-live' },
  { method: 'POST', path: '/api/arm/motors/enable', description: 'Re-engage motors after learning mode', status: 'implemented-live' },
  { method: 'POST', path: '/api/arm/gripper/open', description: 'Open the attached gripper', status: 'implemented-live' },
  { method: 'POST', path: '/api/arm/gripper/close', description: 'Close / grasp with the attached gripper', status: 'implemented-live' },
  { method: 'POST', path: '/api/arm/gripper/raw', description: 'Raw 1-parameter OPEN/CLOSE_GRIPPER test', status: 'implemented-live' },
  { method: 'POST', path: '/api/arm/tool/scan', description: 'Detect the currently attached tool', status: 'implemented-live' },
  { method: 'POST', path: '/api/arm/home', description: 'Fold the arm to the PyNiryo home pose', status: 'implemented-live' },
  { method: 'POST', path: '/api/arm/recover', description: 'Slowly retract to the last safe point after a collision/fault', status: 'implemented-live' },
  { method: 'POST', path: '/api/arm/fault/clear', description: 'Acknowledge a motion fault after inspection', status: 'implemented-live' },
  { method: 'POST', path: '/api/arm/calibrate', description: 'Auto-calibrate the arm (full-range sweep)', status: 'implemented-live' },
  { method: 'GET', path: '/api/camera/stream', description: 'MJPEG proxy from the Jetson RealSense bridge', status: 'implemented-live' },
  { method: 'POST', path: '/api/vision/qr', description: 'Detect a QR code in a clicked/dragged camera ROI', status: 'implemented-live' },
  { method: 'GET', path: '/api/vehicle', description: 'Live pull-mode AGV online/battery/transport status', status: 'implemented-live' },
  { method: 'GET', path: '/api/arm/poses', description: 'List saved joint poses', status: 'implemented-live' },
  { method: 'POST', path: '/api/arm/poses', description: 'Save the current pose under a name (upsert)', status: 'implemented-live' },
  { method: 'DELETE', path: '/api/arm/poses/:name', description: 'Delete a saved pose', status: 'implemented-live' },
  { method: 'POST', path: '/api/arm/poses/:name/apply', description: 'Move to a saved pose (linear Cartesian by default)', status: 'implemented-live' },
  { method: 'GET', path: '/api/arm/sequences', description: 'List saved pose sequences', status: 'implemented-live' },
  { method: 'POST', path: '/api/arm/sequences', description: 'Save / overwrite a named sequence of poses', status: 'implemented-live' },
  { method: 'DELETE', path: '/api/arm/sequences/:name', description: 'Delete a saved sequence', status: 'implemented-live' },
]
