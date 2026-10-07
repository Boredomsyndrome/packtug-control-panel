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

export const CAGE_BIN = { w: 1150, d: 750, h: 1650, maxWeight: 500 }

export const SKUS: Sku[] = [
  { id: 'SKU-1001', name: 'Frozen Dumplings 1kg×10', category: 'Frozen dim sum', size: { w: 400, d: 300, h: 200 }, weight: 11 },
  { id: 'SKU-1002', name: 'Frozen Buns 1kg×8', category: 'Frozen dim sum', size: { w: 500, d: 350, h: 250 }, weight: 9 },
  { id: 'SKU-1003', name: 'Frozen Beef Carton 10kg', category: 'Frozen meat', size: { w: 600, d: 400, h: 300 }, weight: 15 },
  { id: 'SKU-1004', name: 'Frozen Mixed Veg 500g×12', category: 'Frozen veg', size: { w: 450, d: 300, h: 250 }, weight: 8 },
  { id: 'SKU-1005', name: 'Frozen Fries 2kg×6', category: 'Frozen potato', size: { w: 400, d: 300, h: 300 }, weight: 12 },
  { id: 'SKU-1006', name: 'Frozen Fish Fillet 1kg×10', category: 'Frozen seafood', size: { w: 550, d: 350, h: 200 }, weight: 10 },
  { id: 'SKU-1007', name: 'Frozen Pizza 350g×16', category: 'Frozen bakery', size: { w: 350, d: 350, h: 150 }, weight: 6 },
  { id: 'SKU-1008', name: 'Frozen Dessert 1kg×8', category: 'Frozen dessert', size: { w: 300, d: 250, h: 200 }, weight: 5 },
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
      { skuId: 'SKU-1001', qty: 6 },
      { skuId: 'SKU-1003', qty: 4 },
      { skuId: 'SKU-1005', qty: 8 },
      { skuId: 'SKU-1007', qty: 10 },
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
      { skuId: 'SKU-1002', qty: 12 },
      { skuId: 'SKU-1004', qty: 10 },
      { skuId: 'SKU-1008', qty: 14 },
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
      { skuId: 'SKU-1003', qty: 6 },
      { skuId: 'SKU-1006', qty: 8 },
      { skuId: 'SKU-1001', qty: 6 },
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
      { skuId: 'SKU-1007', qty: 20 },
      { skuId: 'SKU-1008', qty: 12 },
      { skuId: 'SKU-1004', qty: 8 },
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
      { skuId: 'SKU-1005', qty: 14 },
      { skuId: 'SKU-1002', qty: 10 },
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
      { skuId: 'SKU-1006', qty: 10 },
      { skuId: 'SKU-1003', qty: 5 },
      { skuId: 'SKU-1008', qty: 10 },
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

export const CAGES: Cage[] = [
  { id: 'CAGE-01', state: 'loading', zoneId: 'Z-COLD-A', dims: CAGE_BIN, maxWeight: 500, loadVolume: 0.42 * CAGE_BIN.w * CAGE_BIN.d * CAGE_BIN.h, loadWeight: 186, centerOfGravity: { x: 560, y: 370, z: 480 }, orderId: 'ORD-24001' },
  { id: 'CAGE-02', state: 'empty', zoneId: 'Z-COLD-B', dims: CAGE_BIN, maxWeight: 500, loadVolume: 0, loadWeight: 0 },
  { id: 'CAGE-03', state: 'in-transit', zoneId: 'Z-DISPATCH', dims: CAGE_BIN, maxWeight: 500, loadVolume: 0.66 * CAGE_BIN.w * CAGE_BIN.d * CAGE_BIN.h, loadWeight: 274, centerOfGravity: { x: 540, y: 360, z: 690 }, orderId: 'ORD-24005' },
  { id: 'CAGE-04', state: 'staged', zoneId: 'Z-COLD-A', dims: CAGE_BIN, maxWeight: 500, loadVolume: 0.31 * CAGE_BIN.w * CAGE_BIN.d * CAGE_BIN.h, loadWeight: 132, centerOfGravity: { x: 585, y: 390, z: 360 } },
  { id: 'CAGE-05', state: 'empty', zoneId: 'Z-STAGE', dims: CAGE_BIN, maxWeight: 500, loadVolume: 0, loadWeight: 0 },
  { id: 'CAGE-06', state: 'sealed', zoneId: 'Z-DISPATCH', dims: CAGE_BIN, maxWeight: 500, loadVolume: 0.55 * CAGE_BIN.w * CAGE_BIN.d * CAGE_BIN.h, loadWeight: 210, centerOfGravity: { x: 550, y: 365, z: 600 } },
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
