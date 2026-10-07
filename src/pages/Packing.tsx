import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useApp } from '../store/AppStore'
import { Badge, Card, Empty, KpiCard, PageHeader, Progress } from '../components/ui'
import { Cage3DView } from '../components/Cage3DView'
import { kg, pct, volumeLitres } from '../lib/format'

export function Packing() {
  const { orderId } = useParams()
  const { orders, plans, runPacking, skuMap } = useApp()
  const [selected, setSelected] = useState<string>(orderId ?? orders[0]?.id ?? '')
  const [cageIdx, setCageIdx] = useState(0)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (orderId && orderId !== selected) setSelected(orderId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderId])

  const order = useMemo(() => orders.find((o) => o.id === selected), [orders, selected])
  const plan = selected ? plans[selected] : undefined

  useEffect(() => {
    if (!order || plan || busy) return
    let active = true
    setBusy(true)
    runPacking(order.id).finally(() => active && setBusy(false))
    return () => {
      active = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, plan])

  useEffect(() => {
    setCageIdx(0)
  }, [selected])

  const cage = plan?.cages[Math.min(cageIdx, (plan?.cages.length ?? 1) - 1)]

  const totalWeight = order
    ? order.items.reduce((acc, it) => acc + (skuMap[it.skuId]?.weight ?? 0) * it.qty, 0)
    : 0

  return (
    <>
      <PageHeader
        title="3D Packing"
        desc="Mixed-size carton packing into roll cages — cage count, utilisation and placement plan."
        actions={
          <>
            <select value={selected} onChange={(e) => setSelected(e.target.value)} style={{ width: 220 }}>
              {orders.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.id} · {o.branch}
                </option>
              ))}
            </select>
            <button
              className="btn primary"
              disabled={!order || busy}
              onClick={() => order && runPacking(order.id)}
            >
              {busy ? 'Computing…' : 'Recompute plan'}
            </button>
          </>
        }
      />

      {!order ? (
        <Card>
          <Empty>Select an order to pack.</Empty>
        </Card>
      ) : (
        <>
          <div className="grid cols-4">
            <KpiCard label="Cages required" value={plan?.cages.length ?? '—'} foot={`bin ${plan?.bin.w ?? 1150}×${plan?.bin.d ?? 750}×${plan?.bin.h ?? 1650} mm`} tone="violet" />
            <KpiCard label="Overall utilisation" value={plan ? pct(plan.totalUtil) : '—'} foot="filled volume / cage volume" tone="green" />
            <KpiCard label="Cartons" value={plan?.itemCount ?? '—'} foot={`total weight ${kg(totalWeight)}`} tone="cyan" />
            <KpiCard label="Max cage weight" value={`${plan?.bin.maxWeight ?? 500} kg`} foot="payload limit (safety)" tone="amber" />
          </div>

          <div className="grid split">
            <Card
              title={`Cage ${cageIdx + 1} of ${plan?.cages.length ?? 0}`}
              hint={cage ? `${pct(cage.volumeUtil)} full · ${kg(cage.loadWeight)} · fill height ${cage.fillHeight} mm` : ''}
              right={
                plan && plan.cages.length > 1 ? (
                  <div className="row">
                    {plan.cages.map((_, i) => (
                      <button key={i} className={`btn sm ${i === cageIdx ? 'primary' : 'ghost'}`} onClick={() => setCageIdx(i)}>
                        {i + 1}
                      </button>
                    ))}
                  </div>
                ) : undefined
              }
            >
              {cage ? <Cage3DView cage={cage} /> : <Empty>No cages in plan.</Empty>}
            </Card>

            <div className="stack">
              <Card title="Algorithm" hint="server-side target: skjolber/3d-bin-container-packing">
                <div className="stack" style={{ fontSize: 12.5 }}>
                  <div className="row between">
                    <span className="muted">Engine</span>
                    <span>{plan?.algorithm ?? '—'}</span>
                  </div>
                  <div className="row between">
                    <span className="muted">Strategies</span>
                    <span>
                      <Badge tone="cyan">LAFF</Badge> <Badge tone="violet">brute-force</Badge>
                    </span>
                  </div>
                  <div className="row between">
                    <span className="muted">Constraints</span>
                    <span className="faint">orientation · stability · payload</span>
                  </div>
                  <div className="row between">
                    <span className="muted">Generated</span>
                    <span className="faint">{plan ? new Date(plan.generatedAt).toLocaleTimeString('en-GB') : '—'}</span>
                  </div>
                </div>
                {plan && (
                  <div style={{ marginTop: 12 }}>
                    <div className="row between" style={{ fontSize: 12 }}>
                      <span className="muted">Average utilisation</span>
                      <span>{pct(plan.totalUtil)}</span>
                    </div>
                    <Progress value={plan.totalUtil} tone="green" />
                  </div>
                )}
              </Card>

              <Card title="Placements" hint={cage ? `${cage.placements.length} items in cage ${cageIdx + 1}` : ''}>
                <div style={{ maxHeight: 330, overflow: 'auto' }}>
                  <table className="data">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>SKU</th>
                        <th>Pos (x,y,z)</th>
                        <th>Size</th>
                        <th>Vol</th>
                      </tr>
                    </thead>
                    <tbody>
                      {cage?.placements.map((p, i) => (
                        <tr key={i}>
                          <td className="faint">{i + 1}</td>
                          <td className="tag">{p.skuId}</td>
                          <td className="mono muted" style={{ fontSize: 11.5 }}>
                            {p.x},{p.y},{p.z}
                          </td>
                          <td className="mono muted" style={{ fontSize: 11.5 }}>
                            {p.w}×{p.d}×{p.h}
                          </td>
                          <td className="muted">{volumeLitres(p.w * p.d * p.h)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </div>
          </div>
        </>
      )}
    </>
  )
}
