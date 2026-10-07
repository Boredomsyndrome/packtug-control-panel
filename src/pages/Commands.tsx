import { useState } from 'react'
import { useApp } from '../store/AppStore'
import { Badge, Card, PageHeader } from '../components/ui'
import { clock } from '../lib/format'

export function Commands() {
  const { orders, agvs, zones, loadOrderOntoAgv, reoptimiseZone, activities } = useApp()

  const [orderId, setOrderId] = useState(orders.find((o) => o.status !== 'done')?.id ?? orders[0]?.id ?? '')
  const [agvId, setAgvId] = useState(agvs.find((a) => a.status === 'idle')?.id ?? agvs[0]?.id ?? '')
  const [zoneId, setZoneId] = useState(zones[0]?.id ?? '')
  const [strategy, setStrategy] = useState<'LAFF' | 'brute-force'>('LAFF')
  const [busy, setBusy] = useState(false)

  const idleAgvs = agvs.filter((a) => a.status === 'idle' && a.battery > 25)

  async function runLoad() {
    if (!orderId || !agvId) return
    setBusy(true)
    await loadOrderOntoAgv(orderId, agvId)
    setBusy(false)
  }

  async function runReopt() {
    if (!zoneId) return
    await reoptimiseZone(zoneId)
  }

  return (
    <>
      <PageHeader title="Commands" desc="High-level operator commands. Wired to the reserved API layer (mock results for now)." />

      <div className="grid cols-2">
        <Card title="Load order onto AGV" hint="POST /api/orders/:id/dispatch">
          <div className="stack">
            <div className="field">
              <label>Order</label>
              <select value={orderId} onChange={(e) => setOrderId(e.target.value)}>
                {orders.filter((o) => o.status !== 'done').map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.id} · {o.branch} · {o.status}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>AGV</label>
              <select value={agvId} onChange={(e) => setAgvId(e.target.value)}>
                {agvs.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.id} · {a.status} · {a.battery.toFixed(0)}%
                  </option>
                ))}
              </select>
            </div>
            <div className="row">
              <button className="btn primary" disabled={busy || !idleAgvs.length} onClick={runLoad}>
                {busy ? 'Dispatching…' : '⌘ Load order onto AGV'}
              </button>
              {!idleAgvs.length && <span className="faint" style={{ fontSize: 12 }}>No idle AGV (battery &gt; 25%).</span>}
            </div>
            <p className="faint" style={{ fontSize: 12, margin: 0 }}>
              Runs the packing plan, then assigns the order and starts a pick mission.
            </p>
          </div>
        </Card>

        <Card title="Re-optimise packing of zone" hint="POST /api/zones/:id/reoptimise">
          <div className="stack">
            <div className="field">
              <label>Zone</label>
              <select value={zoneId} onChange={(e) => setZoneId(e.target.value)}>
                {zones.map((z) => (
                  <option key={z.id} value={z.id}>
                    {z.name} ({z.id})
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>Strategy</label>
              <select value={strategy} onChange={(e) => setStrategy(e.target.value as 'LAFF' | 'brute-force')}>
                <option value="LAFF">LAFF (fast)</option>
                <option value="brute-force">brute-force (optimal)</option>
              </select>
            </div>
            <div className="row">
              <button className="btn" onClick={runReopt}>
                ↻ Re-optimise zone
              </button>
            </div>
            <p className="faint" style={{ fontSize: 12, margin: 0 }}>
              Server-side 3D bin packing recomputes cage loading for the whole zone.
            </p>
          </div>
        </Card>
      </div>

      <Card title="Command log" hint="activity feed">
        <div>
          {activities.slice(0, 14).map((a) => (
            <div className="activity" key={a.id}>
              <span className="t">{clock(a.time)}</span>
              <span className={`lv-${a.level}`}>●</span>
              <span>{a.text}</span>
            </div>
          ))}
          {activities.length === 0 && <div className="empty">No commands yet.</div>}
        </div>
      </Card>

      <div className="row">
        <Badge tone="amber" dot>All commands are simulated until the backend + S200 are connected</Badge>
      </div>
    </>
  )
}
