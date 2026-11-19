import type { Mission, Order, OrderItem, PackingPlan, OrderPriority } from '../types'
import { packBoxes, type BinSpec, type PackBox } from '../lib/packing'
import { API_BASE, MOCK_MODE } from './endpoints'

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms))
const uid = (p: string) => `${p}-${Math.random().toString(36).slice(2, 6).toUpperCase()}`

export interface CreateOrderInput {
  branch: string
  priority: OrderPriority
  dueAt: string
  items: OrderItem[]
}

export interface PackRequest {
  orderId: string
  boxes: PackBox[]
  bin: BinSpec
  algorithm?: string
}

export interface DispatchInput {
  orderId: string
  agvId: string
}

export type MissionAction = 'pause' | 'resume' | 'abort'

export interface LoadOrderInput {
  orderId: string
  agvId: string
}

export interface ReoptimiseInput {
  zoneId: string
  strategy?: 'LAFF' | 'brute-force'
}

export interface IntegrationStatus {
  apiBase: string
  mockMode: boolean
  backend: 'mock' | 'online' | 'offline'
  rosBridge: 'offline' | 'online'
  hardwareConnected: boolean
}

export interface ArmPose {
  x: number
  y: number
  z: number
  roll: number
  pitch: number
  yaw: number
}

export interface ArmStatus {
  connected: boolean
  mock: boolean
  host: string | null
  joints_deg: number[] | null
  pose: ArmPose | null
  calibration_needed: boolean | null
  learning_mode: boolean | null
  max_velocity: number
  velocity_limit: number
  max_reach_m: number
  tool_extension_m: number
  reach_m: number | null
  max_step_deg: number
  max_request_deg: number
  max_pose_deg: number
  limits_deg: [number, number][]
  tool: string | null
  gripper: string
  busy: boolean
  error: string | null
  fault: string | null
  collision: boolean
  recovery_available: boolean
  recovery_target_deg: number[] | null
  collision_api: 'hardware' | 'motion-error' | 'unknown'
  updated_at: number | null
}

export interface QrDetection {
  ok: boolean
  frame: { w: number; h: number }
  roi?: { x: number; y: number; w: number; h: number }
  center?: { x: number; y: number }
  corners?: Array<{ x: number; y: number }>
  angle_deg?: number
  area_px?: number
  error?: string
}

export interface VisionCalibrationPoint {
  px: number
  py: number
  arm_x: number
  arm_y: number
}

export interface VisionCalibration {
  points: VisionCalibrationPoint[]
  homography: number[][] | null
  rmse_m: number | null
  needed: number
}

export interface VisionMapping {
  ok: boolean
  x: number
  y: number
}

export interface PickPathVec3 {
  x: number
  y: number
  z: number
}

/** Cage pose in the arm base frame (metres, degrees) plus the outer box. */
export interface CageGeometry {
  x: number
  y: number
  z: number
  yaw: number
  width: number
  depth: number
  height: number
  opening_margin: number
  clearance_m: number
  /** the real cage has no front-top bar */
  front_top_open?: boolean
}

export interface CageFitInfo {
  width_m: number
  depth_m: number
  height_m: number
  yaw_deg: number
  origin: PickPathVec3
  center: PickPathVec3
  edge_spread_m: { width_m: number; depth_m: number; height_m: number }
  orthogonality: number
  fit_method?: string
  warnings: string[]
  ignored_corners?: number[]
  reconstructed_corners?: number[]
  reordered_corners?: number[]
}

export interface CageFitResult {
  ok: boolean
  cage: CageGeometry
  fit: CageFitInfo
}

export interface PickPathRequest {
  /** null = use the current arm pose */
  start?: ArmPose | null
  /** omitted = no via point */
  via?: ArmPose
  /** optional home transit between pick and place */
  home?: ArmPose
  target: ArmPose
  approach_m?: number
  gate_clearance_m?: number
  cage?: CageGeometry
  /** eight taught corners override cage pose/size */
  corners?: PickPathVec3[]
  /** corner indices to ignore (only 6 and 7 are supported) */
  ignored_corners?: number[]
}

export interface PickPathPoint {
  kind: string
  pose: ArmPose
  zone: 'outside' | 'inside' | 'gate'
  reach_m: number
  issues: string[]
}

export interface PickPathSegment {
  from: string
  to: string
  length_m: number
  min_clearance_m: number | null
  collision: boolean
  issues: string[]
}

export interface PickPathResult {
  ok: boolean
  dry_run: boolean
  cage_source?: 'dimensions' | 'corners'
  cage_fit?: CageFitInfo | null
  points: PickPathPoint[]
  segments: PickPathSegment[]
  warnings: string[]
  actions?: Array<{ at: string; action: string }>
  opening?: {
    center: PickPathVec3
    gate: PickPathVec3
    normal_out: PickPathVec3
    width_m: number
    height_m: number
    front_top_open?: boolean
  }
  limits?: { max_reach_m: number; z_min_m: number; z_max_m: number }
}

export interface ExecutePickStep {
  step: string
  ok: boolean
  percent?: number
  pose?: ArmPose
  angle_deg?: number
  requested_deg?: number
  substeps?: number
  skipped?: boolean
}

export interface ExecutePickResult {
  ok: boolean
  stage: string
  steps: ExecutePickStep[]
  retract?: PickPathResult
  plan?: PickPathResult
  error?: string
}

export interface ExecutePickRequest {
  target: ArmPose
  /** recorded home pose; execution moves to home + home_lift_m */
  home?: ArmPose
  home_lift_m?: number
  /** horizontal pull-back distance toward the base before lowering */
  retract_m?: number
    /** Legacy field; placement always levels and aligns the gripper. */
  preserve_orientation?: boolean
    /** Legacy field; true is rejected during level placement. */
  fold_j2_to_min?: boolean
  approach_m?: number
  gate_clearance_m?: number
  cage?: CageGeometry
  corners?: PickPathVec3[]
  ignored_corners?: number[]
  speed_percent?: number
  return_home_after?: boolean
}

export interface ArmPosePreset {
  name: string
  joints_deg: number[]
  radius_m: number
  created_at: string | null
  updated_at: string | null
}

export const ARM_SEQUENCE_GRIPPER_OPEN = '__gripper_open__'
export const ARM_SEQUENCE_GRIPPER_CLOSE = '__gripper_close__'

export interface ArmSequence {
  name: string
  poses: string[]
  dwell_sec: number
  transit: string | null
  created_at: string | null
  updated_at: string | null
}

const ARM_LIMITS_DEG: [number, number][] = [
  [-168.47, 168.47],
  [-104.35, 34.45],
  [-76.5, 85.0],
  [-119.19, 119.19],
  [-99.5, 109.5],
  [-144.46, 144.46],
]

const mockArm = {
  connected: false,
  host: null as string | null,
  joints: [0, -15, 20, 0, 0, 0],
  pose: { x: 0.3, y: 0, z: 0.2, roll: 180, pitch: 0, yaw: 90 },
  calibrationNeeded: false,
  learningMode: false,
  tool: null as string | null,
  gripper: 'unknown',
  maxVelocity: 5,
  maxReach: 0.47,
  error: null as string | null,
}

function mockArmStatus(): ArmStatus {
  return {
    connected: mockArm.connected,
    mock: true,
    host: mockArm.host,
    joints_deg: [...mockArm.joints],
    pose: { ...mockArm.pose },
    calibration_needed: mockArm.calibrationNeeded,
    learning_mode: mockArm.learningMode,
    max_velocity: mockArm.maxVelocity,
    velocity_limit: 20,
    max_reach_m: mockArm.maxReach,
    tool_extension_m: 0.1,
    reach_m: null,
    max_step_deg: 60,
    max_request_deg: 60,
    max_pose_deg: 180,
    limits_deg: ARM_LIMITS_DEG,
    tool: mockArm.tool,
    gripper: mockArm.gripper,
    busy: false,
    error: mockArm.error,
    fault: null,
    collision: false,
    recovery_available: false,
    recovery_target_deg: null,
    collision_api: 'motion-error',
    updated_at: Math.floor(Date.now() / 1000),
  }
}

const mockPoses: ArmPosePreset[] = []
const mockSequences: ArmSequence[] = []

function mockArmMove(target: number[], maxDelta = 60) {
  if (!mockArm.connected) throw new Error('arm is not connected')
  if (mockArm.calibrationNeeded) throw new Error('calibration required')
  if (mockArm.learningMode) throw new Error('motors are released')
  target.forEach((value, i) => {
    const [lo, hi] = ARM_LIMITS_DEG[i]
    if (value < lo || value > hi) throw new Error(`joint ${i + 1} outside limits`)
    if (Math.abs(value - mockArm.joints[i]) > maxDelta) {
      throw new Error(`joint ${i + 1} move exceeds the ${maxDelta}° limit`)
    }
  })
  mockArm.joints = target.slice()
}

async function jsonRequest<T>(
  path: string,
  body?: unknown,
  method?: 'GET' | 'POST' | 'DELETE',
): Promise<T> {
  const verb = method ?? (body === undefined ? 'GET' : 'POST')
  const res = await fetch(`${API_BASE}${path}`, {
    method: verb,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const data = await res.json()
      if (typeof data.detail === 'string') detail = data.detail
    } catch {
      // response was not JSON — keep the status code
    }
    throw new Error(detail)
  }
  return (await res.json()) as T
}

function logCommand(name: string, payload: unknown) {
  // Reserved integration boundary — replace with the real fetch() call.
  // eslint-disable-next-line no-console
  console.info(`[api:mock] ${name}`, MOCK_MODE ? payload : `POST ${API_BASE}`)
}

export const api = {
  async getIntegrationStatus(): Promise<IntegrationStatus> {
    if (MOCK_MODE) {
      await delay(150)
      return {
        apiBase: API_BASE,
        mockMode: true,
        backend: 'mock',
        rosBridge: 'offline',
        hardwareConnected: false,
      }
    }
    try {
      await jsonRequest<{ status: string }>('/health')
      return {
        apiBase: API_BASE,
        mockMode: false,
        backend: 'online',
        rosBridge: 'offline',
        hardwareConnected: false,
      }
    } catch {
      return {
        apiBase: API_BASE,
        mockMode: false,
        backend: 'offline',
        rosBridge: 'offline',
        hardwareConnected: false,
      }
    }
  },

  /** POST /api/orders — create a picking order. */
  async createOrder(input: CreateOrderInput): Promise<Order> {
    logCommand('createOrder', input)
    await delay(200)
    return {
      id: uid('ORD'),
      branch: input.branch,
      priority: input.priority,
      status: 'pending',
      createdAt: new Date().toISOString(),
      dueAt: input.dueAt,
      items: input.items,
    }
  },

  /** POST /api/orders/:id/pack — server-side 3D bin packing. */
  async packOrder(req: PackRequest): Promise<PackingPlan> {
    logCommand('packOrder', { orderId: req.orderId, items: req.boxes.length })
    await delay(450)
    const { cages, totalUtil, itemCount } = packBoxes(req.boxes, req.bin)
    return {
      orderId: req.orderId,
      bin: req.bin,
      cages,
      totalUtil,
      itemCount,
      generatedAt: new Date().toISOString(),
      algorithm: req.algorithm ?? 'ExtremePoint-FirstFit (client prototype)',
    }
  },

  /** POST /api/orders/:id/dispatch — assign an order to an AGV. */
  async dispatchOrder(input: DispatchInput): Promise<Mission> {
    logCommand('dispatchOrder', input)
    await delay(200)
    return {
      id: uid('MSN'),
      type: 'pick',
      status: 'queued',
      orderId: input.orderId,
      agvId: input.agvId,
      progress: 0,
      steps: [],
      createdAt: new Date().toISOString(),
    }
  },

  /** POST /api/missions/:id/control — pause / resume / abort. */
  async controlMission(missionId: string, action: MissionAction): Promise<{ missionId: string; action: MissionAction }> {
    logCommand('controlMission', { missionId, action })
    await delay(120)
    return { missionId, action }
  },

  /** High-level command: "load order X onto AGV Y". */
  async loadOrderOntoAgv(input: LoadOrderInput): Promise<{ accepted: boolean; missionId: string }> {
    logCommand('loadOrderOntoAgv', input)
    await delay(250)
    return { accepted: true, missionId: uid('MSN') }
  },

  /** High-level command: "re-optimise packing of zone Z". */
  async reoptimiseZone(input: ReoptimiseInput): Promise<{ accepted: boolean; proposedUtil: number; strategy: string }> {
    logCommand('reoptimiseZone', input)
    await delay(500)
    return {
      accepted: true,
      proposedUtil: 0.72 + Math.random() * 0.2,
      strategy: input.strategy ?? 'LAFF',
    }
  },

  // ---------- Ned1 arm bridge (live via server/arm_server.py) ----------

  async getArmStatus(): Promise<ArmStatus> {
    if (MOCK_MODE) {
      await delay(80)
      return mockArmStatus()
    }
    return jsonRequest<ArmStatus>('/arm/status')
  },

  async detectQr(
    x: number,
    y: number,
    radius = 60,
    roi?: { x: number; y: number; w: number; h: number },
  ): Promise<QrDetection> {
    return jsonRequest<QrDetection>('/vision/qr', {
      x,
      y,
      radius,
      ...(roi ? { roi_x: roi.x, roi_y: roi.y, roi_w: roi.w, roi_h: roi.h } : {}),
    })
  },

  async getVisionCalibration(): Promise<VisionCalibration> {
    return jsonRequest<VisionCalibration>('/vision/calibration')
  },

  async addVisionCalibrationPoint(point: VisionCalibrationPoint): Promise<VisionCalibration> {
    return jsonRequest<VisionCalibration>('/vision/calibration/point', point)
  },

  async solveVisionCalibration(): Promise<VisionCalibration> {
    return jsonRequest<VisionCalibration>('/vision/calibration/solve', {})
  },

  async resetVisionCalibration(): Promise<VisionCalibration> {
    return jsonRequest<VisionCalibration>('/vision/calibration', undefined, 'DELETE')
  },

  async mapVisionPixel(x: number, y: number): Promise<VisionMapping> {
    return jsonRequest<VisionMapping>('/vision/map', { x, y })
  },

  /**
   * Dry-run Cartesian pick path with cage frame clearance checks.
   * Always live: the planner needs the bridge's current pose and limits.
   */
  async planArmPickPath(request: PickPathRequest): Promise<PickPathResult> {
    return jsonRequest<PickPathResult>('/arm/pick_path', request)
  },

  /** Fit an ideal cage box to eight taught corners (no motion). */
  async fitArmCage(
    points: PickPathVec3[],
    options?: {
      opening_margin?: number
      clearance_m?: number
      front_top_open?: boolean
      ignored_corners?: number[]
    },
  ): Promise<CageFitResult> {
    return jsonRequest<CageFitResult>('/arm/cage_fit', { points, ...options })
  },

  /** Blocking pick/place: close at start, home, place at target, open. */
  async executeArmPick(request: ExecutePickRequest): Promise<ExecutePickResult> {
    return jsonRequest<ExecutePickResult>('/arm/execute_pick', request)
  },

  async connectArm(ip: string): Promise<ArmStatus> {
    if (MOCK_MODE) {
      await delay(250)
      mockArm.connected = true
      mockArm.host = ip
      mockArm.tool = 'GRIPPER_1'
      mockArm.gripper = 'unknown'
      mockArm.error = null
      return mockArmStatus()
    }
    return jsonRequest<ArmStatus>('/arm/connect', { ip })
  },

  async disconnectArm(): Promise<ArmStatus> {
    if (MOCK_MODE) {
      await delay(120)
      mockArm.connected = false
      mockArm.host = null
      mockArm.tool = null
      mockArm.gripper = 'unknown'
      return mockArmStatus()
    }
    return jsonRequest<ArmStatus>('/arm/disconnect', {})
  },

  async jogArmJoint(joint: number, deltaDeg: number): Promise<ArmStatus> {
    if (MOCK_MODE) {
      await delay(150)
      const target = [...mockArm.joints]
      target[joint - 1] += deltaDeg
      mockArmMove(target)
      return mockArmStatus()
    }
    return jsonRequest<ArmStatus>('/arm/jog', { joint, delta_deg: deltaDeg })
  },

  async moveArmJoints(jointsDeg: number[]): Promise<ArmStatus> {
    if (MOCK_MODE) {
      await delay(250)
      mockArmMove(jointsDeg)
      return mockArmStatus()
    }
    return jsonRequest<ArmStatus>('/arm/joints', { joints_deg: jointsDeg })
  },

  async moveArmJoint(joint: number, angleDeg: number): Promise<ArmStatus> {
    if (MOCK_MODE) {
      await delay(200)
      const target = [...mockArm.joints]
      target[joint - 1] = angleDeg
      mockArmMove(target)
      return mockArmStatus()
    }
    return jsonRequest<ArmStatus>('/arm/joint', { joint, angle_deg: angleDeg })
  },

  async enableArmMotors(): Promise<ArmStatus> {
    if (MOCK_MODE) {
      await delay(120)
      mockArm.learningMode = false
      return mockArmStatus()
    }
    return jsonRequest<ArmStatus>('/arm/motors/enable', {})
  },

  /** Change the arm speed cap (1–20%); applied on the next move immediately. */
  async setArmVelocity(percent: number): Promise<ArmStatus> {
    if (MOCK_MODE) {
      await delay(120)
      mockArm.maxVelocity = Math.max(1, Math.min(20, percent))
      return mockArmStatus()
    }
    return jsonRequest<ArmStatus>('/arm/velocity', { percent })
  },

  /** Set the max horizontal gripper-tip reach (metres). */
  async setArmReach(maxReachM: number): Promise<ArmStatus> {
    if (MOCK_MODE) {
      await delay(80)
      mockArm.maxReach = Math.max(0.15, Math.min(0.5, maxReachM))
      return mockArmStatus()
    }
    return jsonRequest<ArmStatus>('/arm/reach', { max_reach_m: maxReachM })
  },

  /** Fold to the PyNiryo home pose (safe transit / park). */
  async homeArm(): Promise<ArmStatus> {
    if (MOCK_MODE) {
      await delay(300)
      if (!mockArm.connected) throw new Error('arm is not connected')
      mockArm.joints = [0, -15, 20, 0, 0, 0]
      return mockArmStatus()
    }
    return jsonRequest<ArmStatus>('/arm/home', {})
  },

  /** Retract slowly to the last safe joint position after a collision/fault. */
  async recoverArm(): Promise<ArmStatus> {
    if (MOCK_MODE) {
      await delay(300)
      if (!mockArm.connected) throw new Error('arm is not connected')
      mockArm.error = null
      return mockArmStatus()
    }
    return jsonRequest<ArmStatus>('/arm/recover', {})
  },

  /** Acknowledge a motion fault after inspecting the arm by hand. */
  async clearArmFault(): Promise<ArmStatus> {
    if (MOCK_MODE) {
      await delay(100)
      if (!mockArm.connected) throw new Error('arm is not connected')
      mockArm.error = null
      return mockArmStatus()
    }
    return jsonRequest<ArmStatus>('/arm/fault/clear', {})
  },

  /** Auto-calibration sweeps the full joint range (~30 s); workspace must be clear. */
  async calibrateArm(): Promise<ArmStatus> {
    if (MOCK_MODE) {
      await delay(1200)
      if (!mockArm.connected) throw new Error('arm is not connected')
      mockArm.joints = [0, -15, 20, 0, 0, 0]
      mockArm.calibrationNeeded = false
      mockArm.learningMode = false
      return mockArmStatus()
    }
    return jsonRequest<ArmStatus>('/arm/calibrate', { confirm: true })
  },

  /** Release motors (manual posing) or re-engage them. */
  async setArmLearning(enabled: boolean): Promise<ArmStatus> {
    if (MOCK_MODE) {
      await delay(150)
      if (!mockArm.connected) throw new Error('arm is not connected')
      mockArm.learningMode = enabled
      return mockArmStatus()
    }
    return jsonRequest<ArmStatus>('/arm/learning', { enabled, confirm: enabled })
  },

  async openArmGripper(): Promise<ArmStatus> {
    if (MOCK_MODE) {
      await delay(200)
      if (!mockArm.connected) throw new Error('arm is not connected')
      if (mockArm.learningMode) throw new Error('motors are released')
      mockArm.gripper = 'open'
      return mockArmStatus()
    }
    return jsonRequest<ArmStatus>('/arm/gripper/open', {})
  },

  async closeArmGripper(): Promise<ArmStatus> {
    if (MOCK_MODE) {
      await delay(200)
      if (!mockArm.connected) throw new Error('arm is not connected')
      if (mockArm.learningMode) throw new Error('motors are released')
      mockArm.gripper = 'closed'
      return mockArmStatus()
    }
    return jsonRequest<ArmStatus>('/arm/gripper/close', {})
  },

  /** Firmware-specific 1-parameter OPEN/CLOSE_GRIPPER (speed 100–1000). */
  async rawArmGripper(openGripper: boolean, speed = 500): Promise<ArmStatus> {
    if (MOCK_MODE) {
      await delay(200)
      if (!mockArm.connected) throw new Error('arm is not connected')
      if (mockArm.learningMode) throw new Error('motors are released')
      mockArm.gripper = openGripper ? 'open' : 'closed'
      return mockArmStatus()
    }
    return jsonRequest<ArmStatus>('/arm/gripper/raw', { open: openGripper, speed })
  },

  async scanArmTool(): Promise<ArmStatus> {
    if (MOCK_MODE) {
      await delay(200)
      if (!mockArm.connected) throw new Error('arm is not connected')
      mockArm.tool = 'GRIPPER_1'
      mockArm.gripper = 'unknown'
      return mockArmStatus()
    }
    return jsonRequest<ArmStatus>('/arm/tool/scan', {})
  },

  async listArmPoses(): Promise<ArmPosePreset[]> {
    if (MOCK_MODE) {
      await delay(80)
      return mockPoses.map((p) => ({ ...p, joints_deg: [...p.joints_deg] }))
    }
    const data = await jsonRequest<{ poses: ArmPosePreset[] }>('/arm/poses')
    return data.poses
  },

  async saveArmPose(name: string, jointsDeg?: number[]): Promise<ArmPosePreset> {
    const trimmed = name.trim()
    if (!trimmed) throw new Error('pose name is required')
    if (MOCK_MODE) {
      await delay(120)
      if (!jointsDeg && !mockArm.connected) throw new Error('arm is not connected')
      const joints = [...(jointsDeg ?? mockArm.joints)]
      const now = new Date().toISOString()
      const existing = mockPoses.find((p) => p.name === trimmed)
      if (existing) {
        existing.joints_deg = joints
        existing.updated_at = now
        return { ...existing, joints_deg: [...existing.joints_deg] }
      }
      const pose: ArmPosePreset = { name: trimmed, joints_deg: joints, radius_m: 0, created_at: now, updated_at: now }
      mockPoses.push(pose)
      return { ...pose, joints_deg: [...pose.joints_deg] }
    }
    const data = await jsonRequest<{ pose: ArmPosePreset; poses: ArmPosePreset[] }>('/arm/poses', {
      name: trimmed,
      joints_deg: jointsDeg,
    })
    return data.pose
  },

  async deleteArmPose(name: string): Promise<void> {
    if (MOCK_MODE) {
      await delay(100)
      const index = mockPoses.findIndex((p) => p.name === name)
      if (index >= 0) mockPoses.splice(index, 1)
      return
    }
    await jsonRequest<{ poses: ArmPosePreset[] }>(
      `/arm/poses/${encodeURIComponent(name)}`,
      undefined,
      'DELETE',
    )
  },

  async applyArmPose(name: string, linear = true): Promise<ArmStatus> {
    if (MOCK_MODE) {
      await delay(300)
      const pose = mockPoses.find((p) => p.name === name)
      if (!pose) throw new Error(`pose not found: ${name}`)
      mockArmMove(pose.joints_deg, 180)
      return mockArmStatus()
    }
    return jsonRequest<ArmStatus>(`/arm/poses/${encodeURIComponent(name)}/apply`, { linear })
  },

  async listArmSequences(): Promise<ArmSequence[]> {
    if (MOCK_MODE) {
      await delay(80)
      return mockSequences.map((s) => ({ ...s, poses: [...s.poses] }))
    }
    const data = await jsonRequest<{ sequences: ArmSequence[] }>('/arm/sequences')
    return data.sequences
  },

  async saveArmSequence(
    name: string,
    poses: string[],
    dwellSec: number,
    transit: string | null = null,
  ): Promise<ArmSequence> {
    const trimmed = name.trim()
    if (!trimmed) throw new Error('sequence name is required')
    if (!poses.length) throw new Error('add at least one pose')
    const via = transit?.trim() || null
    if (MOCK_MODE) {
      await delay(120)
      const missing = poses.filter((p) => p !== ARM_SEQUENCE_GRIPPER_OPEN && p !== ARM_SEQUENCE_GRIPPER_CLOSE && !mockPoses.some((known) => known.name === p))
      if (via && via !== '__home__' && !mockPoses.some((known) => known.name === via)) missing.push(via)
      if (missing.length) throw new Error(`unknown pose(s): ${missing.join(', ')}`)
      const now = new Date().toISOString()
      const existing = mockSequences.find((s) => s.name === trimmed)
      if (existing) {
        existing.poses = [...poses]
        existing.dwell_sec = dwellSec
        existing.transit = via
        existing.updated_at = now
        return { ...existing, poses: [...existing.poses] }
      }
      const sequence: ArmSequence = {
        name: trimmed,
        poses: [...poses],
        dwell_sec: dwellSec,
        transit: via,
        created_at: now,
        updated_at: now,
      }
      mockSequences.push(sequence)
      return { ...sequence, poses: [...sequence.poses] }
    }
    const data = await jsonRequest<{ sequence: ArmSequence; sequences: ArmSequence[] }>('/arm/sequences', {
      name: trimmed,
      poses,
      dwell_sec: dwellSec,
      transit: via,
    })
    return data.sequence
  },

  async deleteArmSequence(name: string): Promise<void> {
    if (MOCK_MODE) {
      await delay(100)
      const index = mockSequences.findIndex((s) => s.name === name)
      if (index >= 0) mockSequences.splice(index, 1)
      return
    }
    await jsonRequest<{ sequences: ArmSequence[] }>(
      `/arm/sequences/${encodeURIComponent(name)}`,
      undefined,
      'DELETE',
    )
  },
}
