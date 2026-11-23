import { API_BASE } from './endpoints'

export interface VehicleCommand {
  vx: number
  wz: number
}

export interface LiveRobot {
  id: string
  online: boolean
  battery_v: number | null
  battery_age: number | null
  cmd: VehicleCommand
  cmd_age: number | null
  applied: VehicleCommand
  estop: boolean
  watchdog_trips: number
  camera: boolean
  scan?: boolean
  transport: string | null
  ws_connected?: boolean
  odom?: number[] | null     // v3.7: /odom [x, y, yaw] passthrough (m, rad)
  nav_mode?: string          // off | mapping | localizing | route
  max_lin: number
  max_rot: number
  uptime: number
  seq: number
}

export interface ScanFrame {
  ranges: number[]
  amin: number
  ainc: number
  rmin: number
  rmax: number
}

export interface ScanBody {
  ok: boolean
  age: number | null
  fresh: boolean
  scan: ScanFrame | null
}

export interface LiveFleet {
  ok: boolean
  robots: LiveRobot[]
  camera_path?: string
}

async function request<T>(path: string, init?: RequestInit, timeoutMs = 4000): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`${API_BASE}${path}`, { ...init, signal: controller.signal })
    let payload: unknown = null
    try {
      payload = await response.json()
    } catch {
      // empty/non-JSON error
    }
    if (!response.ok) {
      const detail =
        payload && typeof payload === 'object' && 'detail' in payload
          ? String((payload as { detail: unknown }).detail)
          : `HTTP ${response.status}`
      throw new Error(detail)
    }
    return payload as T
  } finally {
    window.clearTimeout(timer)
  }
}

export const vehicleApi = {
  getFleet(): Promise<LiveFleet> {
    return request<LiveFleet>('/vehicle')
  },
  /** v3.7 fused origin: the LiDAR ring comes straight off the hub robot
   * API (same site — no bridge hop). */
  getScan(rid: string): Promise<ScanBody> {
    return request<ScanBody>(`/robot/${encodeURIComponent(rid)}/scan`)
  },
  /** v3.7 CS-B4: bridge liveness/mock probe for the topbar chip. */
  getHealth(timeoutMs = 3000): Promise<{ status?: string; mock?: boolean } | null> {
    return request<{ status?: string; mock?: boolean }>('/health', undefined, timeoutMs)
      .then((h) => h)
      .catch(() => null)
  },
}
