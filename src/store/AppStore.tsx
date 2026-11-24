import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import type {
  Agv,
  Cage,
  Mission,
  MissionStep,
  Order,
  OrderItem,
  OrderPriority,
  PackingPlan,
  Role,
  Sku,
  SkuInput,
  ThroughputPoint,
  Zone,
} from '../types'
import { AGVS, CAGES, CAGE_BIN, MISSIONS, ORDERS, SKUS, THROUGHPUT, ZONES } from '../data/mock'
import { DEMO_ORDER_ID, DEMO_STACK_PLAN } from '../data/demoStack'
import { MANUAL_TEST_ORDER_ID, MANUAL_TEST_PLAN } from '../data/manualTest'
import { api } from '../services/api'
import type { PackBox } from '../lib/packing'

export interface Activity {
  id: string
  time: string
  text: string
  level: 'info' | 'success' | 'warn' | 'error'
}

interface AppState {
  skus: Sku[]
  skuMap: Record<string, Sku>
  orders: Order[]
  cages: Cage[]
  agvs: Agv[]
  zones: Zone[]
  missions: Mission[]
  plans: Record<string, PackingPlan>
  throughput: ThroughputPoint[]
  activities: Activity[]
  role: Role
  userName: string
  authenticated: boolean
}

interface AppActions {
  login: (name: string, role: Role) => void
  logout: () => void
  setRole: (role: Role) => void
  createOrder: (input: { branch: string; priority: OrderPriority; dueAt: string; items: OrderItem[] }) => Promise<void>
  addSku: (input: SkuInput) => Sku
  removeSku: (id: string) => void
  runPacking: (orderId: string) => Promise<PackingPlan | undefined>
  dispatchOrder: (orderId: string, agvId: string) => Promise<void>
  controlMission: (missionId: string, action: 'pause' | 'resume' | 'abort') => Promise<void>
  loadOrderOntoAgv: (orderId: string, agvId: string) => Promise<void>
  reoptimiseZone: (zoneId: string) => Promise<number>
  pushActivity: (text: string, level?: Activity['level']) => void
}

type Store = AppState & AppActions

const AppContext = createContext<Store | null>(null)

let activitySeq = 0
const nowIso = () => new Date().toISOString()

function expandOrder(order: Order, skuMap: Record<string, Sku>): PackBox[] {
  const boxes: PackBox[] = []
  for (const item of order.items) {
    const sku = skuMap[item.skuId]
    if (!sku) continue
    for (let i = 0; i < item.qty; i++) {
      boxes.push({
        skuId: sku.id,
        name: sku.name,
        w: sku.size.w,
        d: sku.size.d,
        h: sku.size.h,
        weight: sku.weight,
        fixedOrientation: sku.fixedOrientation,
      })
    }
  }
  return boxes
}

function buildPickSteps(order: Order, skuMap: Record<string, Sku>): MissionStep[] {
  const first = skuMap[order.items[0]?.skuId]
  return [
    { id: 's1', label: `Navigate to shelf (${first?.category ?? 'storage'})`, status: 'pending' },
    { id: 's2', label: 'RFID recognise & grasp', status: 'pending' },
    { id: 's3', label: 'Place into cage (packing-guided)', status: 'pending' },
    { id: 's4', label: 'Weight / centre-of-gravity check', status: 'pending' },
    { id: 's5', label: 'Return to staging', status: 'pending' },
  ]
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [skus, setSkus] = useState<Sku[]>(SKUS)
  const skuMap = useMemo(() => Object.fromEntries(skus.map((s) => [s.id, s])), [skus])

  const [orders, setOrders] = useState<Order[]>(ORDERS)
  const [cages, setCages] = useState<Cage[]>(CAGES)
  const [agvs, setAgvs] = useState<Agv[]>(AGVS)
  const [zones] = useState<Zone[]>(ZONES)
  const [missions, setMissions] = useState<Mission[]>(MISSIONS)
  const [plans, setPlans] = useState<Record<string, PackingPlan>>({})
  const [throughput, setThroughput] = useState<ThroughputPoint[]>(THROUGHPUT)
  const [activities, setActivities] = useState<Activity[]>([
    { id: 'a0', time: nowIso(), text: 'Control panel started in mock mode — hardware not connected', level: 'warn' },
  ])
  const [role, setRole] = useState<Role>('supervisor')
  const [userName, setUserName] = useState('Operator')
  const [authenticated, setAuthenticated] = useState(true)

  const tickRef = useRef(0)
  const missionsRef = useRef<Mission[]>(missions)
  useEffect(() => {
    missionsRef.current = missions
  }, [missions])

  const pushActivity = useCallback((text: string, level: Activity['level'] = 'info') => {
    setActivities((prev) =>
      [{ id: `a${++activitySeq}`, time: nowIso(), text, level }, ...prev].slice(0, 60),
    )
  }, [])

  const login = useCallback(
    (name: string, r: Role) => {
      setUserName(name)
      setRole(r)
      setAuthenticated(true)
      pushActivity(`Signed in as ${name} (${r})`, 'success')
    },
    [pushActivity],
  )

  const logout = useCallback(() => {
    setAuthenticated(false)
  }, [])

  const createOrder = useCallback<AppActions['createOrder']>(
    async (input) => {
      const order = await api.createOrder(input)
      setOrders((prev) => [order, ...prev])
      pushActivity(`Order ${order.id} created for ${order.branch}`, 'success')
    },
    [pushActivity],
  )

  const addSku = useCallback<AppActions['addSku']>(
    (input) => {
      const nums = skus
        .map((s) => Number(s.id.replace(/\D/g, '')))
        .filter((n) => Number.isFinite(n) && n > 0)
      const id = `SKU-${(nums.length ? Math.max(...nums) : 1000) + 1}`
      const sku: Sku = { id, name: input.name, category: input.category, size: input.size, weight: input.weight }
      setSkus((prev) => [...prev, sku])
      pushActivity(`Goods ${id} added · ${sku.name} (${sku.size.w}×${sku.size.d}×${sku.size.h} mm, ${sku.weight} kg)`, 'success')
      return sku
    },
    [skus, pushActivity],
  )

  const removeSku = useCallback<AppActions['removeSku']>(
    (id) => {
      setSkus((prev) => prev.filter((s) => s.id !== id))
      pushActivity(`Goods ${id} removed`, 'warn')
    },
    [pushActivity],
  )

  const runPacking = useCallback<AppActions['runPacking']>(
    async (orderId) => {
      const order = orders.find((o) => o.id === orderId)
      if (!order) return undefined
      pushActivity(`Running 3D packing for ${orderId}…`)
      const plan =
        orderId === DEMO_ORDER_ID
          ? { ...DEMO_STACK_PLAN, generatedAt: nowIso() }
          : orderId === MANUAL_TEST_ORDER_ID
            ? { ...MANUAL_TEST_PLAN, generatedAt: nowIso() }
            : await api.packOrder({
                orderId,
                boxes: expandOrder(order, skuMap),
                bin: CAGE_BIN,
              })
      setPlans((prev) => ({ ...prev, [orderId]: plan }))
      setOrders((prev) =>
        prev.map((o) => (o.id === orderId && o.status === 'pending' ? { ...o, status: 'planned' } : o)),
      )
      pushActivity(
        `Packing plan for ${orderId}: ${plan.cages.length} cage(s), ${(plan.totalUtil * 100).toFixed(1)}% utilisation`,
        'success',
      )
      return plan
    },
    [orders, skuMap, pushActivity],
  )

  const dispatchOrder = useCallback<AppActions['dispatchOrder']>(
    async (orderId, agvId) => {
      const order = orders.find((o) => o.id === orderId)
      if (!order) return
      const mission = await api.dispatchOrder({ orderId, agvId })
      const steps = buildPickSteps(order, skuMap)
      const fullMission: Mission = { ...mission, steps, status: 'running', startedAt: nowIso(), etaSec: 480 }
      setMissions((prev) => [fullMission, ...prev])
      setOrders((prev) =>
        prev.map((o) => (o.id === orderId ? { ...o, assignedAgvId: agvId, status: 'picking' } : o)),
      )
      setAgvs((prev) =>
        prev.map((a) => (a.id === agvId ? { ...a, status: 'picking', missionId: fullMission.id } : a)),
      )
      pushActivity(`Dispatched ${fullMission.id}: ${orderId} → ${agvId}`, 'success')
    },
    [orders, skuMap, pushActivity],
  )

  const loadOrderOntoAgv = useCallback<AppActions['loadOrderOntoAgv']>(
    async (orderId, agvId) => {
      await api.loadOrderOntoAgv({ orderId, agvId })
      await runPacking(orderId)
      await dispatchOrder(orderId, agvId)
    },
    [runPacking, dispatchOrder],
  )

  const controlMission = useCallback<AppActions['controlMission']>(
    async (missionId, action) => {
      await api.controlMission(missionId, action)
      setMissions((prev) =>
        prev.map((m) => {
          if (m.id !== missionId) return m
          if (action === 'pause') return { ...m, status: 'paused' }
          if (action === 'resume') return { ...m, status: 'running' }
          return { ...m, status: 'failed', endedAt: nowIso() }
        }),
      )
      if (action === 'abort') {
        const aborted = missionsRef.current.find((x) => x.id === missionId)
        if (aborted?.agvId) {
          const agvId = aborted.agvId
          setAgvs((a) => a.map((agv) => (agv.id === agvId ? { ...agv, status: 'idle', missionId: undefined } : agv)))
        }
      }
      pushActivity(`Mission ${missionId} ${action}`, action === 'abort' ? 'error' : 'info')
    },
    [pushActivity],
  )

  const reoptimiseZone = useCallback<AppActions['reoptimiseZone']>(
    async (zoneId) => {
      const res = await api.reoptimiseZone({ zoneId })
      pushActivity(`Zone ${zoneId} re-optimised → ${(res.proposedUtil * 100).toFixed(1)}% (${res.strategy})`, 'success')
      return res.proposedUtil
    },
    [pushActivity],
  )

  // Hardware-free simulation loop — advances missions and moves the (virtual) fleet.
  useEffect(() => {
    const timer = window.setInterval(() => {
      tickRef.current += 1
      const tick = tickRef.current

      const finished: Mission[] = []
      const nextMissions = missionsRef.current.map((m) => {
        if (m.status !== 'running') return m
        const progress = Math.min(1, m.progress + 0.015 + Math.random() * 0.02)
        const stepCount = Math.max(m.steps.length, 1)
        const steps = m.steps.map((s, i) => {
          const boundary = (i + 1) / stepCount
          const status: MissionStep['status'] =
            progress >= boundary ? 'done' : progress >= i / stepCount ? 'running' : 'pending'
          return { ...s, status }
        })
        const updated: Mission = { ...m, progress, steps, etaSec: Math.max(0, Math.round((1 - progress) * 480)) }
        if (progress >= 1) {
          updated.status = 'done'
          updated.endedAt = nowIso()
          finished.push(updated)
        }
        return updated
      })
      setMissions(nextMissions)

      for (const m of finished) {
        if (m.agvId) {
          const agvId = m.agvId
          setAgvs((a) =>
            a.map((agv) => (agv.id === agvId ? { ...agv, status: 'idle', missionId: undefined, cageId: undefined } : agv)),
          )
        }
        if (m.orderId) {
          const orderId = m.orderId
          setOrders((o) => o.map((ord) => (ord.id === orderId ? { ...ord, status: 'done' } : ord)))
        }
      }

      setCages((prev) =>
        prev.map((c) =>
          c.state === 'loading'
            ? { ...c, loadVolume: Math.min(c.dims.w * c.dims.d * c.dims.h * 0.9, c.loadVolume + Math.random() * 1.5e6) }
            : c,
        ),
      )

      if (tick % 4 === 0) {
        setThroughput((prev) => {
          const now = new Date()
          const point: ThroughputPoint = {
            t: `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`,
            orders: 5 + Math.round(Math.random() * 6),
            cages: 7 + Math.round(Math.random() * 6),
            utilisation: 0.6 + Math.random() * 0.32,
          }
          return [...prev.slice(1), point]
        })
      }
    }, 1000)
    return () => window.clearInterval(timer)
  }, [])

  const value: Store = {
    skus,
    skuMap,
    orders,
    cages,
    agvs,
    zones,
    missions,
    plans,
    throughput,
    activities,
    role,
    userName,
    authenticated,
    login,
    logout,
    setRole,
    createOrder,
    addSku,
    removeSku,
    runPacking,
    dispatchOrder,
    controlMission,
    loadOrderOntoAgv,
    reoptimiseZone,
    pushActivity,
  }

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useApp(): Store {
  const ctx = useContext(AppContext)
  if (!ctx) throw new Error('useApp must be used within AppProvider')
  return ctx
}
