import type {
  Agv,
  Cage,
  Mission,
  MissionStep,
  Order,
  Sku,
  ThroughputPoint,
  Zone,
} from '../types'

// Plan A cage — reach-matched to Ned2 (side placement, standard gripper):
// internal 550 (W) x 200 (D) x 400 (H) mm, floor level with the arm base plane.
export const CAGE_BIN = { w: 550, d: 200, h: 400, maxWeight: 20 }

// Demo goods sized for the Ned2: every carton fits the 550x200x400 cage and
// weighs <= 0.3 kg (Ned2 payload).
export const SKUS: Sku[] = [
  { id: 'SKU-1001', name: 'Frozen Dim Sum Box', category: 'Frozen dim sum', size: { w: 200, d: 120, h: 100 }, weight: 0.18 },
  { id: 'SKU-1002', name: 'Frozen Bun Tray', category: 'Frozen dim sum', size: { w: 180, d: 150, h: 120 }, weight: 0.2 },
  { id: 'SKU-1003', name: 'Frozen Meat Pack', category: 'Frozen meat', size: { w: 250, d: 180, h: 150 }, weight: 0.28 },
  { id: 'SKU-1004', name: 'Mixed Veg Pouch', category: 'Frozen veg', size: { w: 160, d: 100, h: 80 }, weight: 0.12 },
  { id: 'SKU-1005', name: 'Frozen Fries Pack', category: 'Frozen potato', size: { w: 220, d: 140, h: 110 }, weight: 0.2 },
  { id: 'SKU-1006', name: 'Fish Fillet Tray', category: 'Frozen seafood', size: { w: 150, d: 120, h: 100 }, weight: 0.15 },
  { id: 'SKU-1007', name: 'Pizza Carton', category: 'Frozen bakery', size: { w: 200, d: 160, h: 90 }, weight: 0.17 },
  { id: 'SKU-1008', name: 'Dessert Cup Tray', category: 'Frozen dessert', size: { w: 120, d: 100, h: 70 }, weight: 0.1 },
]

export const ORDERS: Order[] = [
  {
    id: 'ORD-24001',
    branch: 'Fairwood · Tsuen Wan',
    priority: 'high',
    status: 'picking',
    createdAt: '2026-10-07T08:10:00+08:00',
    dueAt: '2026-10-07T11:30:00+08:00',
    items: [
      { skuId: 'SKU-1001', qty: 3 },
      { skuId: 'SKU-1003', qty: 2 },
      { skuId: 'SKU-1005', qty: 2 },
      { skuId: 'SKU-1008', qty: 4 },
    ],
    assignedAgvId: 'AGV-01',
  },
  {
    id: 'ORD-24002',
    branch: 'Fairwood · Sha Tin',
    priority: 'normal',
    status: 'planned',
    createdAt: '2026-10-07T08:20:00+08:00',
    dueAt: '2026-10-07T12:00:00+08:00',
    items: [
      { skuId: 'SKU-1002', qty: 3 },
      { skuId: 'SKU-1004', qty: 5 },
      { skuId: 'SKU-1006', qty: 4 },
      { skuId: 'SKU-1007', qty: 2 },
    ],
  },
  {
    id: 'ORD-24003',
    branch: 'Fairwood · Kwun Tong',
    priority: 'normal',
    status: 'pending',
    createdAt: '2026-10-07T08:35:00+08:00',
    dueAt: '2026-10-07T12:30:00+08:00',
    items: [
      { skuId: 'SKU-1001', qty: 3 },
      { skuId: 'SKU-1003', qty: 2 },
      { skuId: 'SKU-1006', qty: 3 },
      { skuId: 'SKU-1008', qty: 3 },
    ],
  },
  {
    id: 'ORD-24004',
    branch: 'Fairwood · Mong Kok',
    priority: 'low',
    status: 'pending',
    createdAt: '2026-10-07T09:00:00+08:00',
    dueAt: '2026-10-07T15:00:00+08:00',
    items: [
      { skuId: 'SKU-1004', qty: 8 },
      { skuId: 'SKU-1006', qty: 6 },
      { skuId: 'SKU-1008', qty: 8 },
    ],
  },
  {
    id: 'ORD-24005',
    branch: 'Fairwood · Causeway Bay',
    priority: 'high',
    status: 'transferring',
    createdAt: '2026-10-07T07:40:00+08:00',
    dueAt: '2026-10-07T11:00:00+08:00',
    items: [
      { skuId: 'SKU-1005', qty: 3 },
      { skuId: 'SKU-1002', qty: 3 },
      { skuId: 'SKU-1008', qty: 4 },
    ],
    assignedAgvId: 'AGV-02',
  },
  {
    id: 'ORD-24006',
    branch: 'Fairwood · Yuen Long',
    priority: 'normal',
    status: 'done',
    createdAt: '2026-10-07T07:00:00+08:00',
    dueAt: '2026-10-07T10:30:00+08:00',
    items: [
      { skuId: 'SKU-1002', qty: 4 },
      { skuId: 'SKU-1007', qty: 4 },
      { skuId: 'SKU-1008', qty: 4 },
    ],
    assignedAgvId: 'AGV-03',
  },
]

export const AGVS: Agv[] = [
  {
    id: 'AGV-01',
    model: 'WHEELTEC S200',
    status: 'offline',
    battery: 82,
    pose: { x: 0, y: 0, theta: 0 },
    sim: true,
    lastSeen: new Date().toISOString(),
  },
  {
    id: 'AGV-02',
    model: 'WHEELTEC S200',
    status: 'offline',
    battery: 61,
    pose: { x: 0, y: 0, theta: 0 },
    sim: true,
    lastSeen: new Date().toISOString(),
  },
  {
    id: 'AGV-03',
    model: 'WHEELTEC S200',
    status: 'offline',
    battery: 34,
    pose: { x: 0, y: 0, theta: 0 },
    sim: true,
    lastSeen: new Date().toISOString(),
  },
]

export const ZONES: Zone[] = [
  { id: 'Z-COLD-A', name: 'Cold Store A', kind: 'cold-store', temperatureC: -18, fillRate: 0.74, capacityCages: 40, cages: ['CAGE-01', 'CAGE-04'] },
  { id: 'Z-COLD-B', name: 'Cold Store B', kind: 'cold-store', temperatureC: -22, fillRate: 0.58, capacityCages: 32, cages: ['CAGE-02'] },
  { id: 'Z-STAGE', name: 'Staging', kind: 'staging', temperatureC: 4, fillRate: 0.41, capacityCages: 20, cages: ['CAGE-05'] },
  { id: 'Z-DISPATCH', name: 'Dispatch', kind: 'dispatch', temperatureC: 8, fillRate: 0.3, capacityCages: 16, cages: ['CAGE-03', 'CAGE-06'] },
  { id: 'Z-CHARGE', name: 'Charging Bay', kind: 'charging', temperatureC: 15, fillRate: 0.0, capacityCages: 6, cages: [] },
]

const binVol = CAGE_BIN.w * CAGE_BIN.d * CAGE_BIN.h

export const CAGES: Cage[] = [
  { id: 'CAGE-01', state: 'loading', zoneId: 'Z-COLD-A', dims: CAGE_BIN, maxWeight: CAGE_BIN.maxWeight, loadVolume: 0.4 * binVol, loadWeight: 1.8, centerOfGravity: { x: 260, y: 95, z: 160 }, orderId: 'ORD-24001' },
  { id: 'CAGE-02', state: 'empty', zoneId: 'Z-COLD-B', dims: CAGE_BIN, maxWeight: CAGE_BIN.maxWeight, loadVolume: 0, loadWeight: 0 },
  { id: 'CAGE-03', state: 'in-transit', zoneId: 'Z-DISPATCH', dims: CAGE_BIN, maxWeight: CAGE_BIN.maxWeight, loadVolume: 0.6 * binVol, loadWeight: 2.9, centerOfGravity: { x: 255, y: 100, z: 220 }, orderId: 'ORD-24005' },
  { id: 'CAGE-04', state: 'staged', zoneId: 'Z-COLD-A', dims: CAGE_BIN, maxWeight: CAGE_BIN.maxWeight, loadVolume: 0.3 * binVol, loadWeight: 1.3, centerOfGravity: { x: 268, y: 92, z: 130 } },
  { id: 'CAGE-05', state: 'empty', zoneId: 'Z-STAGE', dims: CAGE_BIN, maxWeight: CAGE_BIN.maxWeight, loadVolume: 0, loadWeight: 0 },
  { id: 'CAGE-06', state: 'sealed', zoneId: 'Z-DISPATCH', dims: CAGE_BIN, maxWeight: CAGE_BIN.maxWeight, loadVolume: 0.55 * binVol, loadWeight: 2.4, centerOfGravity: { x: 270, y: 98, z: 200 } },
]

function step(id: string, label: string, status: MissionStep['status']): MissionStep {
  return { id, label, status }
}

export const MISSIONS: Mission[] = [
  {
    id: 'MSN-9001',
    type: 'pick',
    status: 'running',
    orderId: 'ORD-24001',
    agvId: 'AGV-01',
    cageId: 'CAGE-01',
    progress: 0.45,
    createdAt: '2026-10-07T09:05:00+08:00',
    startedAt: '2026-10-07T09:06:00+08:00',
    etaSec: 420,
    steps: [
      step('s1', 'Navigate to shelf A-12', 'done'),
      step('s2', 'Recognise SKU-1001', 'done'),
      step('s3', 'Pick & place into CAGE-01', 'running'),
      step('s4', 'Return to staging', 'pending'),
    ],
  },
  {
    id: 'MSN-9002',
    type: 'transport',
    status: 'running',
    orderId: 'ORD-24005',
    agvId: 'AGV-02',
    cageId: 'CAGE-03',
    progress: 0.7,
    createdAt: '2026-10-07T08:55:00+08:00',
    startedAt: '2026-10-07T08:58:00+08:00',
    etaSec: 150,
    steps: [
      step('s1', 'Seal CAGE-03', 'done'),
      step('s2', 'Call goods-lift', 'done'),
      step('s3', 'Transfer to dispatch', 'running'),
    ],
  },
  {
    id: 'MSN-9003',
    type: 'charge',
    status: 'running',
    agvId: 'AGV-03',
    progress: 0.6,
    createdAt: '2026-10-07T09:20:00+08:00',
    startedAt: '2026-10-07T09:20:00+08:00',
    etaSec: 900,
    steps: [
      step('s1', 'Dock to charger 2', 'done'),
      step('s2', 'Charging to 80%', 'running'),
    ],
  },
  {
    id: 'MSN-9004',
    type: 'pack',
    status: 'queued',
    orderId: 'ORD-24003',
    progress: 0,
    createdAt: '2026-10-07T09:30:00+08:00',
    steps: [
      step('s1', 'Compute packing plan', 'pending'),
      step('s2', 'Verify centre of gravity', 'pending'),
    ],
  },
]

function seedThroughput(): ThroughputPoint[] {
  const points: ThroughputPoint[] = []
  const base = new Date('2026-10-07T00:00:00+08:00')
  for (let i = 0; i < 12; i++) {
    const t = new Date(base.getTime() + i * 3600 * 1000)
    const orders = 6 + Math.round(Math.sin(i / 2) * 2 + Math.random() * 3)
    points.push({
      t: `${String(t.getHours()).padStart(2, '0')}:00`,
      orders,
      cages: Math.round(orders * (1.1 + Math.random() * 0.5)),
      utilisation: Math.min(0.94, 0.58 + Math.random() * 0.3),
    })
  }
  return points
}

export const THROUGHPUT: ThroughputPoint[] = seedThroughput()

export const STRATEGIES = ['LAFF', 'brute-force'] as const
