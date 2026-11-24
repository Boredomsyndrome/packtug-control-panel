import type { CagePlan, PackingPlan, Placement } from '../types'
import { CAGE_BIN } from './mock'

export const MANUAL_TEST_ORDER_ID = 'ORD-TEST-MANUAL'

const CARTON = {
  'TEST-BOX-A': { name: 'Test box A', w: 200, d: 55, h: 120, weight: 0.2 },
  'TEST-BOX-B': { name: 'Test box B', w: 200, d: 55, h: 160, weight: 0.2 },
  'TEST-BOX-C': { name: 'Test box C', w: 250, d: 55, h: 160, weight: 0.2 },
  'DEMO-BOX': { name: 'Demo box', w: 152, d: 85, h: 110, weight: 0.2 },
} as const

type CartonId = keyof typeof CARTON

function placement(
  seq: number,
  skuId: CartonId,
  x: number,
  y: number,
  z: number,
  support = 1,
): Placement {
  return { seq, skuId, x, y, z, support, ...CARTON[skuId] }
}

const placements: Placement[] = [
  placement(1, 'TEST-BOX-B', 20, 169, 0),
  placement(2, 'TEST-BOX-B', 20, 112, 0),
  placement(3, 'TEST-BOX-A', 20, 56, 0),
  placement(4, 'TEST-BOX-A', 20, 0, 0),
  placement(5, 'TEST-BOX-C', 260, 169, 0),
  placement(6, 'DEMO-BOX', 329, 81, 0),
]

const usedVolume = placements.reduce((sum, p) => sum + p.w * p.d * p.h, 0)
const binVolume = CAGE_BIN.w * CAGE_BIN.d * CAGE_BIN.h
const cage: CagePlan = {
  cageIndex: 0,
  placements,
  volumeUtil: usedVolume / binVolume,
  loadWeight: placements.reduce((sum, p) => sum + p.weight, 0),
  fillHeight: Math.max(...placements.map((p) => p.z + p.h)),
}

export const MANUAL_TEST_PLAN: PackingPlan = {
  orderId: MANUAL_TEST_ORDER_ID,
  bin: { ...CAGE_BIN },
  cages: [cage],
  totalUtil: usedVolume / binVolume,
  itemCount: placements.length,
  generatedAt: new Date().toISOString(),
  algorithm: 'manual-test-layout',
  payloadRadiusM: 0,
  targetZOffsetM: 0,
}
