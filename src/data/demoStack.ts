import type { CagePlan, PackingPlan, Placement } from '../types'
import { CAGE_BIN } from './mock'

export const DEMO_ORDER_ID = 'ORD-STACK-5'
export const DEMO_SKU_ID = 'DEMO-BOX'

const BOX = {
  skuId: DEMO_SKU_ID,
  name: 'Stack demo box',
  w: 152,
  d: 85,
  h: 110,
  weight: 0.2,
  support: 1,
}

function placement(seq: number, x: number, y: number, z: number): Placement {
  return { seq, x, y, z, ...BOX }
}

// Four boxes on the floor in a 2x2 grid, one centered on the back row.
// The rows are pushed toward the back wall for a safer centre of gravity.
// Cage frame: X = 0..550 width, Y = 0..250 depth, Z = 0..400 height.
const placements: Placement[] = [
  placement(1, 119, 160, 0),
  placement(2, 280, 160, 0),
  placement(3, 199, 160, 110),
  placement(4, 119, 65, 0),
  placement(5, 280, 65, 0),
]

const boxVolume = BOX.w * BOX.d * BOX.h
const binVolume = CAGE_BIN.w * CAGE_BIN.d * CAGE_BIN.h
const usedVolume = boxVolume * placements.length

placements[2].support = 0.94

const cage: CagePlan = {
  cageIndex: 0,
  placements,
  volumeUtil: usedVolume / binVolume,
  loadWeight: BOX.weight * placements.length,
  fillHeight: 220,
}

export const DEMO_STACK_PLAN: PackingPlan = {
  orderId: DEMO_ORDER_ID,
  bin: { ...CAGE_BIN },
  cages: [cage],
  totalUtil: usedVolume / binVolume,
  itemCount: placements.length,
  generatedAt: new Date().toISOString(),
  algorithm: 'demo-stack',
}
