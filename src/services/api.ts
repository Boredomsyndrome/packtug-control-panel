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

function logCommand(name: string, payload: unknown) {
  // Reserved integration boundary — replace with the real fetch() call.
  // eslint-disable-next-line no-console
  console.info(`[api:mock] ${name}`, MOCK_MODE ? payload : `POST ${API_BASE}`)
}

export const api = {
  async getIntegrationStatus(): Promise<IntegrationStatus> {
    await delay(150)
    return {
      apiBase: API_BASE,
      mockMode: MOCK_MODE,
      backend: MOCK_MODE ? 'mock' : 'online',
      rosBridge: 'offline',
      hardwareConnected: false,
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
}
