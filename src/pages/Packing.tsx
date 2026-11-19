import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useApp } from '../store/AppStore'
import { Badge, Card, Empty, KpiCard, PageHeader, Progress } from '../components/ui'
import { Cage3DView } from '../components/Cage3DView'
import { hueForSku, kg, pct, volumeLitres } from '../lib/format'
import {
  ARM_TARGET_STORAGE_KEY,
  CAGE_STORAGE_KEY,
  placementToArmTarget,
  placementToLocalTarget,
  type ArmTargetFromPacking,
  type TargetReference,
} from '../lib/digitalTwin'
import type { CageGeometry } from '../services/api'

export function Packing() {
  const { orderId } = useParams()
  const navigate = useNavigate()
  const { orders, plans, runPacking, skuMap } = useApp()
  const [selected, setSelected] = useState<string>(
    orderId ?? orders.find((o) => o.status === 'pending')?.id ?? orders[0]?.id ?? '',
  )
  const [cageIdx, setCageIdx] = useState(0)
  const [busy, setBusy] = useState(false)
  const [step, setStep] = useState(0)
  const [playing, setPlaying] = useState(true)
  const [speed, setSpeed] = useState(1)
  const [twinCage, setTwinCage] = useState<CageGeometry | null>(null)
  const [twinSeq, setTwinSeq] = useState<number | null>(null)
  const [twinReference, setTwinReference] = useState<TargetReference>('web')

  useEffect(() => {
    try {
      const raw = localStorage.getItem(CAGE_STORAGE_KEY)
      const parsed = raw ? (JSON.parse(raw) as { cage?: CageGeometry }) : null
      if (parsed?.cage) setTwinCage(parsed.cage)
    } catch {
      setTwinCage(null)
    }
  }, [])

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

  const cage = plan?.cages[Math.min(cageIdx, (plan?.cages.length ?? 1) - 1)]
  const total = cage?.placements.length ?? 0
  const minSupport = cage && cage.placements.length ? Math.min(...cage.placements.map((p) => p.support)) : 1
  const twinPlacement = cage?.placements.find((p) => p.seq === twinSeq) ?? cage?.placements[0] ?? null
  const twinLocal = twinPlacement ? placementToLocalTarget(twinPlacement, twinReference) : null
  const twinTarget = useMemo(
    () => (twinPlacement && twinCage ? placementToArmTarget(twinPlacement, twinCage, twinReference) : null),
    [twinPlacement, twinCage, twinReference],
  )

  // restart playback whenever the order, cage tab or plan changes
  useEffect(() => {
    setStep(0)
    setPlaying(true)
  }, [selected, cageIdx, plan?.generatedAt])

  // playback timer
  useEffect(() => {
    if (!playing) return
    if (step >= total) {
      setPlaying(false)
      return
    }
    const id = window.setTimeout(() => setStep((s) => Math.min(total, s + 1)), 760 / speed)
    return () => window.clearTimeout(id)
  }, [playing, step, speed, total])

  const totalWeight = order
    ? order.items.reduce((acc, it) => acc + (skuMap[it.skuId]?.weight ?? 0) * it.qty, 0)
    : 0

  function playPause() {
    if (step >= total) {
      setStep(0)
      setPlaying(true)
      return
    }
    setPlaying((p) => !p)
  }
  function nudge(delta: number) {
    setPlaying(false)
    setStep((s) => Math.max(0, Math.min(total, s + delta)))
  }

  function sendTwinTarget() {
    if (!twinPlacement || !twinTarget) return
    const payload: ArmTargetFromPacking = {
      ...twinTarget,
      placementSeq: twinPlacement.seq,
      skuId: twinPlacement.skuId,
      reference: twinReference,
    }
    localStorage.setItem(ARM_TARGET_STORAGE_KEY, JSON.stringify(payload))
    navigate('/arm')
  }

  return (
    <>
      <PageHeader
        title="3D Packing"
        desc="Mixed-size carton packing into roll cages. The animation follows the order the robotic arm places cartons."
        actions={
          <>
            <select value={selected} onChange={(e) => setSelected(e.target.value)} style={{ width: 220 }}>
              {orders.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.id} · {o.branch}
                </option>
              ))}
            </select>
            <button className="btn primary" disabled={!order || busy} onClick={() => order && runPacking(order.id)}>
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
            <KpiCard label="Cages required" value={plan?.cages.length ?? '—'} foot={`bin ${plan?.bin.w ?? 550}×${plan?.bin.d ?? 250}×${plan?.bin.h ?? 400} mm`} tone="violet" />
            <KpiCard label="Overall utilisation" value={plan ? pct(plan.totalUtil) : '—'} foot="filled volume / cage volume" tone="green" />
            <KpiCard label="Cartons" value={plan?.itemCount ?? '—'} foot={`total weight ${kg(totalWeight)}`} tone="cyan" />
            <KpiCard
              label="Min base support"
              value={cage ? pct(minSupport, 0) : '—'}
              foot={`support ≥ 60% · cage ≤ ${plan?.bin.maxWeight ?? 20} kg · arm ≤ 0.3 kg`}
              tone={minSupport < 0.7 ? 'amber' : 'green'}
            />
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
              {cage ? (
                <>
                  <Cage3DView cage={cage} bin={plan?.bin} visibleCount={step} dropKey={step} />

                  <div className="row between" style={{ marginTop: 14 }}>
                    <div className="row" style={{ gap: 6 }}>
                      <button className="btn sm" title="Reset" onClick={() => { setPlaying(false); setStep(0) }}>
                        ⏮
                      </button>
                      <button className="btn sm" title="Previous" onClick={() => nudge(-1)}>
                        ◀
                      </button>
                      <button className="btn sm primary" title="Play / Pause" onClick={playPause} style={{ minWidth: 74 }}>
                        {playing ? '⏸ Pause' : '▶ Play'}
                      </button>
                      <button className="btn sm" title="Next" onClick={() => nudge(1)}>
                        ▶
                      </button>
                      <span className="faint" style={{ marginLeft: 6 }}>
                        <span className="mono" style={{ color: 'var(--accent)' }}>{step}</span>/{total} placed
                      </span>
                    </div>
                    <div className="row" style={{ gap: 6 }}>
                      <label className="faint" style={{ fontSize: 12 }}>
                        Speed
                      </label>
                      <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} style={{ width: 78 }}>
                        <option value={0.5}>0.5×</option>
                        <option value={1}>1×</option>
                        <option value={2}>2×</option>
                        <option value={4}>4×</option>
                      </select>
                    </div>
                  </div>

                  <input
                    type="range"
                    min={0}
                    max={total}
                    value={step}
                    onChange={(e) => {
                      setPlaying(false)
                      setStep(Number(e.target.value))
                    }}
                    style={{ marginTop: 10, padding: 0 }}
                  />
                  <p className="faint" style={{ fontSize: 11.5, margin: '4px 0 0' }}>
                    {plan?.algorithm === 'demo-stack'
                      ? 'Demo order: back row → top → front row. The top box is placed before the front row so the arm never reaches over a completed row.'
                      : 'Order: bottom-up, deepest-first. The arm places cartons from the side through the opening (cyan dashed face); top-down is only used where reachable.'}
                  </p>
                </>
              ) : (
                <Empty>No cages in plan.</Empty>
              )}
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
                    <span className="faint">orientation · payload</span>
                  </div>
                  <div className="row between">
                    <span className="muted">Support rule</span>
                    <span className="faint">base support ≥ 60%</span>
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

              <Card
                title="Placement sequence"
                hint={cage ? `${total} cartons · click a row to jump` : ''}
                right={
                  <span className="faint" style={{ fontSize: 12 }}>
                    {step}/{total} placed
                  </span>
                }
              >
                <div style={{ maxHeight: 360, overflow: 'auto' }}>
                  <table className="data">
                    <thead>
                      <tr>
                        <th style={{ width: 42 }}>#</th>
                        <th>SKU</th>
                        <th>Position (x,y,z)</th>
                        <th>Size</th>
                        <th>Support</th>
                        <th>Vol</th>
                      </tr>
                    </thead>
                    <tbody>
                      {cage?.placements.map((p) => {
                        const placed = p.seq <= step
                        const active = p.seq === step
                        return (
                          <tr
                            key={p.seq}
                            onClick={() => {
                              setPlaying(false)
                              setStep(p.seq)
                            }}
                            style={{
                              cursor: 'pointer',
                              background: active ? 'rgba(34,211,238,0.12)' : undefined,
                              opacity: placed ? 1 : 0.42,
                            }}
                          >
                            <td>
                              <span
                                className="mono"
                                style={{ color: active ? 'var(--accent)' : placed ? 'var(--green)' : 'var(--text-faint)' }}
                              >
                                {placed && !active ? '✓' : p.seq}
                              </span>
                            </td>
                            <td>
                              <span className="row" style={{ gap: 6 }}>
                                <i
                                  style={{
                                    display: 'inline-block',
                                    width: 9,
                                    height: 9,
                                    borderRadius: 3,
                                    background: `hsl(${hueForSku(p.skuId)} 70% 52%)`,
                                  }}
                                />
                                <span className="tag">{p.skuId}</span>
                              </span>
                            </td>
                            <td className="mono muted" style={{ fontSize: 11.5 }}>
                              {p.x},{p.y},{p.z}
                            </td>
                            <td className="mono muted" style={{ fontSize: 11.5 }}>
                              {p.w}×{p.d}×{p.h}
                            </td>
                            <td>
                              <span
                                className="mono"
                                style={{ fontSize: 11.5, color: p.support < 0.75 ? 'var(--amber)' : 'var(--text-dim)' }}
                              >
                                {pct(p.support, 0)}
                              </span>
                            </td>
                            <td className="muted">{volumeLitres(p.w * p.d * p.h)}</td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </Card>

              <Card title="Digital twin target" hint="one box · start pose stays manual">
                {!cage || !twinPlacement ? (
                  <p className="faint">Select a packed cage first.</p>
                ) : !twinCage ? (
                  <p className="faint">
                    No cage geometry saved. Open Arm, record/teach the cage geometry first, then come
                    back here.
                  </p>
                ) : (
                  <div className="stack" style={{ fontSize: 12.5 }}>
                    <div className="row between">
                      <span className="muted">Test box</span>
                      <select
                        value={twinPlacement.seq}
                        onChange={(event) => setTwinSeq(Number(event.target.value))}
                        style={{ width: 150 }}
                      >
                        {cage.placements.map((p) => (
                          <option key={p.seq} value={p.seq}>
                            #{p.seq} · {p.skuId}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="row between">
                      <span className="muted">Target reference</span>
                      <select
                        value={twinReference}
                        onChange={(event) => setTwinReference(event.target.value as TargetReference)}
                        style={{ width: 150 }}
                      >
                        <option value="web">Gripper web</option>
                        <option value="center">Box centre</option>
                      </select>
                    </div>
                    <div className="grid cols-2" style={{ gap: 10 }}>
                      <div>
                        <div className="faint">Cage local (mm)</div>
                        <div className="mono">
                          {twinLocal ? `${twinLocal.x.toFixed(0)}, ${twinLocal.y.toFixed(0)}, ${twinLocal.z.toFixed(0)}` : '—'}
                        </div>
                      </div>
                      <div>
                        <div className="faint">Arm base target (m)</div>
                        <div className="mono">
                          {twinTarget
                            ? `${twinTarget.x.toFixed(3)}, ${twinTarget.y.toFixed(3)}, ${twinTarget.z.toFixed(3)}`
                            : '—'}
                        </div>
                      </div>
                    </div>
                    <button className="btn primary" onClick={sendTwinTarget} disabled={!twinTarget}>
                      Send target to Arm →
                    </button>
                    <p className="faint" style={{ margin: 0, fontSize: 11.5 }}>
                      The arm target is converted with the cage origin/yaw saved in Arm. Start and tool
                      orientation must still be recorded manually.
                    </p>
                  </div>
                )}
              </Card>
            </div>
          </div>
        </>
      )}
    </>
  )
}
