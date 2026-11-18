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
  max_lin: number
  max_rot: number
  uptime: number
  seq: number
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
}
