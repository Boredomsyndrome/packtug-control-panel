import { Link, useNavigate, useParams } from 'react-router-dom'
import { useApp } from '../store/AppStore'
import { Badge, Card, Empty, PageHeader, Progress } from '../components/ui'
import { ORDER_STATUS, PRIORITY } from '../components/status'
import { Cage3DView } from '../components/Cage3DView'
import { kg, pct } from '../lib/format'

export function OrderDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { orders, skuMap, plans, runPacking, dispatchOrder, agvs } = useApp()
  const order = orders.find((o) => o.id === id)

  if (!order) {
    return (
      <Card>
        <Empty>Order {id} not found.</Empty>
        <div className="row" style={{ justifyContent: 'center' }}>
          <Link className="btn sm" to="/orders">
            ← Back to orders
          </Link>
        </div>
      </Card>
    )
  }

  const plan = plans[order.id]
  const idleAgvs = agvs.filter((a) => a.status === 'idle' && a.battery > 25)

  let volume = 0
  let weight = 0
  for (const it of order.items) {
    const sku = skuMap[it.skuId]
    if (!sku) continue
    volume += sku.size.w * sku.size.d * sku.size.h * it.qty
    weight += sku.weight * it.qty
  }

  return (
    <>
      <PageHeader
        title={order.id}
        desc={`${order.branch} · due ${new Date(order.dueAt).toLocaleString('en-GB')}`}
        actions={
          <>
            <Badge tone={PRIORITY[order.priority].tone}>{PRIORITY[order.priority].label} priority</Badge>
            <Badge tone={ORDER_STATUS[order.status].tone} dot>
              {ORDER_STATUS[order.status].label}
            </Badge>
            <button className="btn primary" onClick={() => runPacking(order.id)}>
              Run 3D packing
            </button>
          </>
        }
      />

      <div className="grid split">
        <Card title="Items" hint={`${order.items.reduce((a, b) => a + b.qty, 0)} cartons · ${kg(weight)} · ${(volume / 1e9).toFixed(2)} m³`}>
          <table className="data">
            <thead>
              <tr>
                <th>SKU</th>
                <th>Name</th>
                <th>Dims (mm)</th>
                <th>Unit wt</th>
                <th>Qty</th>
                <th>Subtotal vol</th>
              </tr>
            </thead>
            <tbody>
              {order.items.map((it) => {
                const sku = skuMap[it.skuId]
                if (!sku) return null
                const v = (sku.size.w * sku.size.d * sku.size.h * it.qty) / 1e6
                return (
                  <tr key={it.skuId}>
                    <td className="tag">{sku.id}</td>
                    <td>{sku.name}</td>
                    <td className="mono muted">
                      {sku.size.w}×{sku.size.d}×{sku.size.h}
                    </td>
                    <td className="muted">{kg(sku.weight)}</td>
                    <td>{it.qty}</td>
                    <td className="muted">{v.toFixed(0)} L</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </Card>

        <div className="stack">
          {plan ? (
            <Card
              title="Packing plan"
              hint={`${plan.algorithm} · ${plan.itemCount} items`}
              right={<Link className="btn sm ghost" to={`/packing/${order.id}`}>Full view →</Link>}
            >
              <div className="grid cols-2">
                <div>
                  <div className="kpi-label">Cages needed</div>
                  <div className="kpi-value" style={{ color: 'var(--violet)' }}>{plan.cages.length}</div>
                </div>
                <div>
                  <div className="kpi-label">Utilisation</div>
                  <div className="kpi-value" style={{ color: 'var(--green)' }}>{pct(plan.totalUtil)}</div>
                </div>
              </div>
              <div style={{ marginTop: 12 }}>
                {plan.cages.map((c) => (
                  <div key={c.cageIndex} style={{ marginBottom: 10 }}>
                    <div className="row between" style={{ fontSize: 12 }}>
                      <span className="muted">Cage {c.cageIndex + 1} · {c.placements.length} items</span>
                      <span className="faint">{pct(c.volumeUtil)} · {c.loadWeight.toFixed(0)} kg</span>
                    </div>
                    <Progress value={c.volumeUtil} tone={c.volumeUtil > 0.85 ? 'green' : undefined} />
                  </div>
                ))}
              </div>
            </Card>
          ) : (
            <Card title="Packing plan">
              <Empty>No plan yet — run 3D packing to compute cage count and placements.</Empty>
            </Card>
          )}

          <Card title="Dispatch">
            {order.assignedAgvId ? (
              <p className="muted" style={{ margin: 0 }}>
                Assigned to <span className="tag">{order.assignedAgvId}</span>. Track progress on the{' '}
                <Link className="tag" to="/missions">
                  missions console
                </Link>
                .
              </p>
            ) : idleAgvs.length ? (
              <div className="row">
                <select
                  defaultValue=""
                  onChange={(e) => e.target.value && dispatchOrder(order.id, e.target.value)}
                  style={{ maxWidth: 180 }}
                >
                  <option value="">Select idle AGV…</option>
                  {idleAgvs.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.id} · {a.battery.toFixed(0)}%
                    </option>
                  ))}
                </select>
                <span className="faint" style={{ fontSize: 12 }}>
                  Assigns order and starts a pick mission.
                </span>
              </div>
            ) : (
              <Empty>No idle AGV available right now.</Empty>
            )}
          </Card>
        </div>
      </div>

      {plan && plan.cages[0] && (
        <Card title="Packed cage preview" hint={`Cage 1 of ${plan.cages.length} · drag-free`}>
          <Cage3DView cage={plan.cages[0]} />
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn sm" onClick={() => navigate(`/packing/${order.id}`)}>
              Open packing console
            </button>
          </div>
        </Card>
      )}
    </>
  )
}
