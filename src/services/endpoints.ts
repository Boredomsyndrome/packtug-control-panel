/**
 * Integration surface — what the fused hub (:8090, single origin) actually
 * serves. v3.7/v3.8 layout, verified against backend/robot/api.py,
 * backend/webctl/asgi.py and backend/arm_server.py create_app():
 *
 *   ONE process, ONE port. backend/webctl/asgi.py dispatches by path prefix:
 *   /api/arm|camera|vision|vehicle|health|docs|openapi.json → the arm/vision
 *   bridge (backend/arm_server.py, mounted in-process); everything else →
 *   the robot-plane FastAPI app. WebSockets are raw-ASGI handlers.
 *
 *   - Robot plane      GET/POST /api/robot…  — browser-side, network-trusted
 *                      (LAN/VPN); NO token. docs/NAV.md describes the nav remote.
 *   - Ingestion plane  /robotctl/<id>/… + /ws/robotctl/<id> — robot→hub
 *                      uplink, per-robot Basic token. Not for integrators.
 *   - Arm/vision bridge live on the same origin (also runnable standalone:
 *                      `python backend/arm_server.py --port 8020`).
 *   - Panel business rows (orders/agvs/zones/…) are still frontend MOCK;
 *                      set VITE_MOCK=false + VITE_API_BASE to wire them.
 */

export const API_BASE = import.meta.env.VITE_API_BASE ?? '/api'
export const MOCK_MODE = (import.meta.env.VITE_MOCK ?? 'true') !== 'false'

export interface EndpointSpec {
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'WS'
  path: string
  description: string
  status: 'implemented-mock' | 'implemented-live' | 'planned'
  group: string
}

const G_ROBOT = 'Robot plane — browser API (network-trusted, no token)'
const G_INGEST = 'Robot ingestion plane (robot → hub, per-robot Basic token)'
const G_WS = 'WebSocket plane (robot uplink & camera viewer)'
const G_DOCS = 'OpenAPI / interactive docs'
const G_ARM = 'Arm bridge — Niryo Ned1 (in-process, same origin)'
const G_VISION = 'Camera & vision bridge'
const G_PANEL = 'Panel business API (frontend mock today)'

export const ENDPOINTS: EndpointSpec[] = [
  // ------------------------------------------------------------ robot plane
  { method: 'GET', path: '/api/robot', description: 'Fleet status: online, battery, E-STOP latch, applied velocity, nav mode', status: 'implemented-live', group: G_ROBOT },
  { method: 'GET', path: '/api/robot/:id', description: 'One robot\'s status (same body as fleet entry)', status: 'implemented-live', group: G_ROBOT },
  { method: 'POST', path: '/api/robot/:id/cmd', description: 'Teleop velocity {vx,wz}; 12 Hz hold from UI keys, watchdog zeroes on loss', status: 'implemented-live', group: G_ROBOT },
  { method: 'POST', path: '/api/robot/:id/stop', description: 'Immediate velocity release / stop', status: 'implemented-live', group: G_ROBOT },
  { method: 'POST', path: '/api/robot/:id/estop', description: 'Full E-STOP: latches the command zero, cancels a running route', status: 'implemented-live', group: G_ROBOT },
  { method: 'GET', path: '/api/robot/:id/scan', description: 'Latest LiDAR scan (packed ranges + age) for the sweep canvas', status: 'implemented-live', group: G_ROBOT },
  { method: 'GET', path: '/api/robot/:id/nav', description: 'Nav state {mode, pose, route progress} + saved waypoints + map list', status: 'implemented-live', group: G_ROBOT },
  { method: 'POST', path: '/api/robot/:id/nav', description: 'Nav remote: map_on/map_off/map_save · nav_on/nav_off · record · align · route (1–256 waypoints) · nav_cancel · delete_map · delete_waypoint · clear_waypoints', status: 'implemented-live', group: G_ROBOT },
  { method: 'GET', path: '/api/robot/:id/maps/:name.pgm', description: 'Saved occupancy map (.pgm or .yaml) for the canvas', status: 'implemented-live', group: G_ROBOT },
  { method: 'GET', path: '/cam/:id', description: 'Live MJPEG camera relay (frames ride the robot websocket)', status: 'implemented-live', group: G_ROBOT },

  // -------------------------------------------------------- ingestion plane
  { method: 'GET', path: '/robotctl/:id/cmd', description: 'Long-poll pending command/nav action; returns the EFFECTIVE command (zero when latched or stale)', status: 'implemented-live', group: G_INGEST },
  { method: 'POST', path: '/robotctl/:id/telemetry', description: 'Status uplink: battery, odom, watchdog trips, ROS topic graph, nav block', status: 'implemented-live', group: G_INGEST },
  { method: 'POST', path: '/robotctl/:id/scan', description: 'LiDAR scan uplink (HTTP fallback; normally rides /ws/robotctl)', status: 'implemented-live', group: G_INGEST },
  { method: 'POST', path: '/robotctl/:id/map', description: 'Map pair upload (.pgm + .yaml, gzip/base64); hub auto-serves it back', status: 'implemented-live', group: G_INGEST },
  { method: 'POST', path: '/robotctl/:id/frame', description: 'MJPEG frame upload (HTTP fallback for the camera plane)', status: 'implemented-live', group: G_INGEST },

  // ------------------------------------------------------------ websocket plane
  { method: 'WS', path: '/ws/robotctl/:id?token=***', description: 'Robot uplink, single-websocket policy: status, cmd, scan, nav, map uploads', status: 'implemented-live', group: G_WS },
  { method: 'WS', path: '/ws/cam/:id', description: 'Browser camera viewer; binary = JPEG preceded by 8-byte float64 timestamp', status: 'implemented-live', group: G_WS },
  { method: 'WS', path: '/ws/telemetry', description: 'Panel telemetry stream (agv/cage/mission/metrics) — mock-era placeholder', status: 'planned', group: G_WS },

  // ------------------------------------------------------------------- docs
  { method: 'GET', path: '/docs', description: 'Robot-plane OpenAPI Swagger UI (schema at /openapi.json; /api/schema & /api/redoc redirect here)', status: 'implemented-live', group: G_DOCS },
  { method: 'GET', path: '/api/docs', description: 'Arm/vision-bridge OpenAPI Swagger UI (schema at /api/openapi.json)', status: 'implemented-live', group: G_DOCS },

  // ------------------------------------------------------------- arm bridge
  { method: 'GET', path: '/api/arm/status', description: 'Ned1 joints, pose, calibration and speed', status: 'implemented-live', group: G_ARM },
  { method: 'GET', path: '/api/arm/hardware', description: 'Controller hardware/firmware status readout', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/connect', description: 'Open a PyNiryo TCP session to the Ned1', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/disconnect', description: 'Close the Ned1 session', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/jog', description: 'Nudge one joint by ≤60° at the current speed cap', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/joint', description: 'Move one joint to a dragged absolute angle (≤60°)', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/joints', description: 'Move all joints to absolute targets (≤60° each)', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/pose', description: 'Move the TCP to an absolute Cartesian pose', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/linear', description: 'Relative Cartesian move dx/dy/dz (roll locked; pitch/yaw offsets allowed)', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/jog_pose', description: 'Relative Cartesian offset through firmware jog mode', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/jog_stream', description: 'Continuous velocity jog stream; the server paces the jog thread', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/jog_stop', description: 'Stop the jog stream thread', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/stop', description: 'Stop all arm motion immediately', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/cage_fit', description: 'Fit the roll-cage box from eight taught corners (no motion)', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/pick_path', description: 'Dry-run Cartesian pick path with cage clearance checks (no motion)', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/execute_pick', description: 'Blocking pick&place: close at pose → home → place → open', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/learning', description: 'Release motors for manual posing / re-engage (confirm required)', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/velocity', description: 'Set the arm speed cap (1–20%)', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/reach', description: 'Set the max horizontal gripper-tip reach', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/motors/enable', description: 'Re-engage motors after learning mode', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/gripper/open', description: 'Open the attached gripper', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/gripper/close', description: 'Close / grasp with the attached gripper', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/gripper/raw', description: 'Raw 1-parameter OPEN/CLOSE_GRIPPER test', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/tool/scan', description: 'Detect the currently attached tool', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/home', description: 'Fold the arm to the PyNiryo home pose', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/recover', description: 'Slowly retract to the last safe point after a collision/fault', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/fault/clear', description: 'Acknowledge a motion fault after inspection', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/calibrate', description: 'Auto-calibrate the arm (full-range sweep, confirm required)', status: 'implemented-live', group: G_ARM },
  { method: 'GET', path: '/api/arm/poses', description: 'List saved joint poses', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/poses', description: 'Save the current pose under a name (upsert)', status: 'implemented-live', group: G_ARM },
  { method: 'DELETE', path: '/api/arm/poses/:name', description: 'Delete a saved pose', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/poses/:name/apply', description: 'Move to a saved pose (linear Cartesian by default)', status: 'implemented-live', group: G_ARM },
  { method: 'GET', path: '/api/arm/sequences', description: 'List saved pose sequences', status: 'implemented-live', group: G_ARM },
  { method: 'POST', path: '/api/arm/sequences', description: 'Save / overwrite a named sequence of poses', status: 'implemented-live', group: G_ARM },
  { method: 'DELETE', path: '/api/arm/sequences/:name', description: 'Delete a saved sequence', status: 'implemented-live', group: G_ARM },

  // -------------------------------------------------------- camera & vision
  { method: 'GET', path: '/api/health', description: 'Bridge health {status, mock, time}', status: 'implemented-live', group: G_VISION },
  { method: 'GET', path: '/api/camera/stream', description: 'Jetson RGB MJPEG proxy (same-origin)', status: 'implemented-live', group: G_VISION },
  { method: 'GET', path: '/api/camera/status', description: 'Jetson camera mode and last-frame ages', status: 'implemented-live', group: G_VISION },
  { method: 'GET', path: '/api/camera/depth', description: 'Colorized Jetson depth MJPEG proxy', status: 'implemented-live', group: G_VISION },
  { method: 'GET', path: '/api/camera/depth_stats', description: 'Depth pipeline stats JSON', status: 'implemented-live', group: G_VISION },
  { method: 'GET', path: '/api/camera/mode?value=', description: 'Switch the Jetson camera mode (rgb | depth)', status: 'implemented-live', group: G_VISION },
  { method: 'POST', path: '/api/vision/qr', description: 'Detect a QR in a clicked/dragged camera ROI + estimate its 3D pose', status: 'implemented-live', group: G_VISION },
  { method: 'GET', path: '/api/vision/hand_eye', description: 'Stored hand-eye calibration (samples, camera→tool transform)', status: 'implemented-live', group: G_VISION },
  { method: 'POST', path: '/api/vision/hand_eye/capture', description: 'Add a ChArUco calibration sample (image + stationary arm pose)', status: 'implemented-live', group: G_VISION },
  { method: 'POST', path: '/api/vision/hand_eye/solve', description: 'Solve the camera→tool transform from stored samples', status: 'implemented-live', group: G_VISION },
  { method: 'DELETE', path: '/api/vision/hand_eye', description: 'Reset hand-eye samples and solution', status: 'implemented-live', group: G_VISION },
  { method: 'GET', path: '/api/vision/calibration', description: 'Pixel→world homography calibration state (6 points needed)', status: 'implemented-live', group: G_VISION },
  { method: 'POST', path: '/api/vision/calibration/point', description: 'Add a pixel↔world calibration point', status: 'implemented-live', group: G_VISION },
  { method: 'POST', path: '/api/vision/calibration/solve', description: 'Solve the homography (returns RMSE in metres)', status: 'implemented-live', group: G_VISION },
  { method: 'DELETE', path: '/api/vision/calibration', description: 'Reset calibration points and homography', status: 'implemented-live', group: G_VISION },
  { method: 'POST', path: '/api/vision/map', description: 'Map an image pixel to world x,y via the solved homography', status: 'implemented-live', group: G_VISION },
  { method: 'GET', path: '/api/vehicle', description: 'Hub fleet status proxied for the panel vehicle card', status: 'implemented-live', group: G_VISION },
  { method: 'GET', path: '/api/vehicle/:id', description: 'One robot\'s hub status (proxy)', status: 'implemented-live', group: G_VISION },
  { method: 'GET', path: '/api/vehicle/:id/camera', description: 'Robot MJPEG stream proxied from the hub /cam plane', status: 'implemented-live', group: G_VISION },

  // ----------------------------------------------------------- panel mock
  { method: 'GET', path: '/api/agvs', description: 'Fleet state: pose, battery, status, mission', status: 'implemented-mock', group: G_PANEL },
  { method: 'GET', path: '/api/cages', description: 'Roll-cage fill level, weight, centre of gravity', status: 'implemented-mock', group: G_PANEL },
  { method: 'GET', path: '/api/zones', description: 'Static storage zones and temperatures', status: 'implemented-mock', group: G_PANEL },
  { method: 'GET', path: '/api/orders', description: 'Order list and status', status: 'implemented-mock', group: G_PANEL },
  { method: 'POST', path: '/api/orders', description: 'Create a picking order', status: 'implemented-mock', group: G_PANEL },
  { method: 'POST', path: '/api/orders/:id/pack', description: 'Run server-side 3D bin packing', status: 'planned', group: G_PANEL },
  { method: 'POST', path: '/api/orders/:id/dispatch', description: 'Assign order to an AGV', status: 'implemented-mock', group: G_PANEL },
  { method: 'GET', path: '/api/missions', description: 'Mission queue and progress', status: 'implemented-mock', group: G_PANEL },
  { method: 'POST', path: '/api/missions/:id/control', description: 'pause / resume / abort a mission', status: 'implemented-mock', group: G_PANEL },
  { method: 'POST', path: '/api/zones/:id/reoptimise', description: 'Re-optimise packing of a zone', status: 'implemented-mock', group: G_PANEL },
  { method: 'GET', path: '/api/metrics/throughput', description: 'Historical throughput and utilisation', status: 'implemented-mock', group: G_PANEL },
  { method: 'POST', path: '/api/auth/login', description: 'RBAC login', status: 'implemented-mock', group: G_PANEL },
]
