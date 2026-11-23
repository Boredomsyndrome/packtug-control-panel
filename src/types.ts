export interface Vec3 {
  x: number
  y: number
  z: number
}

export interface Size3 {
  w: number
  d: number
  h: number
}

export interface Sku {
  id: string
  name: string
  category: string
  /** millimetres: w = x-axis, d = y-axis, h = z-axis (height) */
  size: Size3
  /** kg */
  weight: number
  /** keep w/h facing the cage opening and d along cage depth */
  fixedOrientation?: boolean
}

/** user-entered goods parameters (id is assigned by the store) */
export interface SkuInput {
  name: string
  category: string
  size: Size3
  weight: number
}

export interface OrderItem {
  skuId: string
  qty: number
}

export type OrderPriority = 'low' | 'normal' | 'high'

export type OrderStatus =
  | 'pending'
  | 'planned'
  | 'picking'
  | 'packed'
  | 'transferring'
  | 'done'
  | 'exception'

export interface Order {
  id: string
  branch: string
  priority: OrderPriority
  status: OrderStatus
  createdAt: string
  dueAt: string
  items: OrderItem[]
  assignedAgvId?: string
}

export type CageState = 'empty' | 'loading' | 'sealed' | 'in-transit' | 'staged'

export interface Cage {
  id: string
  state: CageState
  zoneId?: string
  dims: Size3
  maxWeight: number
  loadVolume: number
  loadWeight: number
  centerOfGravity?: Vec3
  orderId?: string
}

export type AgvStatus =
  | 'idle'
  | 'navigating'
  | 'picking'
  | 'towing'
  | 'charging'
  | 'fault'
  | 'offline'

export interface Agv {
  id: string
  model: string
  status: AgvStatus
  /** 0-100 */
  battery: number
  pose: { x: number; y: number; theta: number }
  missionId?: string
  cageId?: string
  /** true until the physical WHEELTEC S200 is connected to the telemetry bridge */
  sim: boolean
  lastSeen: string
}

export type ZoneKind = 'cold-store' | 'staging' | 'dispatch' | 'charging'

export interface Zone {
  id: string
  name: string
  kind: ZoneKind
  temperatureC: number
  /** 0-1 */
  fillRate: number
  capacityCages: number
  cages: string[]
}

export type MissionType = 'pick' | 'pack' | 'transport' | 'charge' | 'handover'
export type MissionStatus = 'queued' | 'running' | 'paused' | 'done' | 'failed'
export type StepStatus = 'pending' | 'running' | 'done' | 'failed'

export interface MissionStep {
  id: string
  label: string
  status: StepStatus
}

export interface Mission {
  id: string
  type: MissionType
  status: MissionStatus
  orderId?: string
  agvId?: string
  cageId?: string
  /** 0-1 */
  progress: number
  steps: MissionStep[]
  createdAt: string
  startedAt?: string
  endedAt?: string
  etaSec?: number
}

export interface Placement {
  /** 1-based placement order the robotic arm executes */
  seq: number
  skuId: string
  name: string
  x: number
  y: number
  z: number
  w: number
  d: number
  h: number
  weight: number
  /** fraction of the base resting on the floor / cartons below (0-1) */
  support: number
}

export interface CagePlan {
  cageIndex: number
  placements: Placement[]
  /** 0-1 volumetric utilisation */
  volumeUtil: number
  loadWeight: number
  /** mm, top of highest stacked box */
  fillHeight: number
}

export interface PackingPlan {
  orderId: string
  bin: Size3 & { maxWeight: number }
  cages: CagePlan[]
  /** 0-1 overall utilisation */
  totalUtil: number
  itemCount: number
  generatedAt: string
  algorithm: string
  /** metres; 0 means tool-only path checking */
  payloadRadiusM?: number
  /** metres; overrides the Arm page target Z default */
  targetZOffsetM?: number
}

export interface ThroughputPoint {
  t: string
  orders: number
  cages: number
  utilisation: number
}

export type Role = 'operator' | 'supervisor' | 'maintenance'

export interface SessionUser {
  name: string
  role: Role
}
