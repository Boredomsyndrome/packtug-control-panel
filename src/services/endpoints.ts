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
 * Set VITE_API_BASE and VITE_MOCK=false in .env to switch off mock mode.
 */

export const API_BASE = import.meta.env.VITE_API_BASE ?? '/api'
export const MOCK_MODE = (import.meta.env.VITE_MOCK ?? 'true') !== 'false'

export interface EndpointSpec {
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'WS'
  path: string
  description: string
  status: 'implemented-mock' | 'planned'
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
]
