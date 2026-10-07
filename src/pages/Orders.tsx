import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useApp } from '../store/AppStore'
import type { OrderItem, OrderPriority } from '../types'
import { Badge, Card, PageHeader } from '../components/ui'
import { ORDER_STATUS, PRIORITY } from '../components/status'
import { kg, pct } from '../lib/format'

function orderMetrics(items: OrderItem[], skuMap: ReturnType<typeof useApp>['skuMap']) {
  let volume = 0
  let weight = 0
  for (const it of items) {
    const sku = skuMap[it.skuId]
    if (!sku) continue
    volume += sku.size.w * sku.size.d * sku.size.h * it.qty
    weight += sku.weight * it.qty
  }
  return { volume, weight }
}

export function Orders() {
  const { orders, skus, skuMap, createOrder, runPacking, dispatchOrder, agvs } = useApp()
  const [showForm, setShowForm] = useState(false)
  const [branch, setBranch] = useState('Fairwood · Central')
  const [priority, setPriority] = useState<OrderPriority>('normal')
  const [dueAt, setDueAt] = useState('2026-10-07T14:00')
  const [items, setItems] = useState<OrderItem[]>([{ skuId: skus[0].id, qty: 4 }])

  const idleAgvs = useMemo(() => agvs.filter((a) => a.status === 'idle' && a.battery > 25), [agvs])

  function addRow() {
    setItems((prev) => [...prev, { skuId: skus[0].id, qty: 2 }])
  }
  function updateRow(i: number, patch: Partial<OrderItem>) {
    setItems((prev) => prev.map((it, idx) => (idx === i ? { ...it, ...patch } : it)))
  }
  function removeRow(i: number) {
    setItems((prev) => prev.filter((_, idx) => idx !== i))
  }

  async function submit() {
    await createOrder({ branch, priority, dueAt: new Date(dueAt).toISOString(), items })
    setShowForm(false)
    setItems([{ skuId: skus[0].id, qty: 4 }])
  }

  return (
    <>
      <PageHeader
        title="Orders"
        desc="Outbound orders per branch. Packing plans drive the pick & place sequence."
        actions={
          <button className="btn primary" onClick={() => setShowForm((s) => !s)}>
            {showForm ? 'Close' : '+ New order'}
          </button>
        }
      />

      {showForm && (
        <Card title="Create order" hint="mock — POST /api/orders">
          <div className="grid cols-3">
            <div className="field">
              <label>Branch</label>
              <input value={branch} onChange={(e) => setBranch(e.target.value)} />
            </div>
            <div className="field">
              <label>Priority</label>
              <select value={priority} onChange={(e) => setPriority(e.target.value as OrderPriority)}>
                <option value="low">low</option>
                <option value="normal">normal</option>
                <option value="high">high</option>
              </select>
            </div>
            <div className="field">
              <label>Due</label>
              <input type="datetime-local" value={dueAt} onChange={(e) => setDueAt(e.target.value)} />
            </div>
          </div>

          <div className="stack" style={{ marginTop: 12 }}>
            <label className="faint" style={{ fontSize: 11.5, textTransform: 'uppercase', letterSpacing: 0.5 }}>
              Items
            </label>
            {items.map((it, i) => (
              <div className="row" key={i}>
                <select value={it.skuId} onChange={(e) => updateRow(i, { skuId: e.target.value })} style={{ flex: 1 }}>
                  {skus.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.id} · {s.name}
                    </option>
                  ))}
                </select>
                <input
                  type="number"
                  min={1}
                  value={it.qty}
                  onChange={(e) => updateRow(i, { qty: Math.max(1, Number(e.target.value)) })}
                  style={{ width: 90 }}
                />
                <button className="btn sm ghost danger" onClick={() => removeRow(i)}>
                  ✕
                </button>
              </div>
            ))}
            <div className="row">
              <button className="btn sm" onClick={addRow}>
                + Add item
              </button>
            </div>
          </div>

          <div className="row" style={{ marginTop: 14 }}>
            <button className="btn primary" onClick={submit}>
              Create order
            </button>
          </div>
        </Card>
      )}

      <Card>
        <table className="data">
          <thead>
            <tr>
              <th>Order</th>
              <th>Branch</th>
              <th>Items</th>
              <th>Volume</th>
              <th>Weight</th>
              <th>Priority</th>
              <th>Status</th>
              <th>AGV</th>
              <th style={{ textAlign: 'right' }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((o) => {
              const { volume, weight } = orderMetrics(o.items, skuMap)
              const count = o.items.reduce((a, b) => a + b.qty, 0)
              return (
                <tr key={o.id}>
                  <td>
                    <Link to={`/orders/${o.id}`} className="tag">
                      {o.id}
                    </Link>
                  </td>
                  <td className="muted">{o.branch}</td>
                  <td>{count}</td>
                  <td className="muted">{(volume / 1e9).toFixed(2)} m³</td>
                  <td className="muted">{kg(weight)}</td>
                  <td>
                    <Badge tone={PRIORITY[o.priority].tone}>{PRIORITY[o.priority].label}</Badge>
                  </td>
                  <td>
                    <Badge tone={ORDER_STATUS[o.status].tone} dot>
                      {ORDER_STATUS[o.status].label}
                    </Badge>
                  </td>
                  <td className="mono">{o.assignedAgvId ?? '—'}</td>
                  <td>
                    <div className="row" style={{ justifyContent: 'flex-end' }}>
                      <button className="btn sm" onClick={() => runPacking(o.id)}>
                        Pack
                      </button>
                      {idleAgvs.length > 0 && o.status !== 'done' && (
                        <select
                          defaultValue=""
                          onChange={(e) => {
                            if (e.target.value) dispatchOrder(o.id, e.target.value)
                          }}
                          style={{ width: 104 }}
                        >
                          <option value="">Dispatch…</option>
                          {idleAgvs.map((a) => (
                            <option key={a.id} value={a.id}>
                              {a.id}
                            </option>
                          ))}
                        </select>
                      )}
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </Card>
      <p className="faint" style={{ fontSize: 12, margin: 0 }}>
        Tip: “Pack” runs the client prototype packer; “Dispatch” assigns the order to an idle AGV and opens a mission. Overall avg plan utilisation target: {pct(0.75, 0)}.
      </p>
    </>
  )
}
