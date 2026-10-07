import { useState } from 'react'
import { useApp } from '../store/AppStore'
import { Badge, Card, PageHeader, Progress } from '../components/ui'
import { ZONE_KIND } from '../components/status'
import { pct } from '../lib/format'

export function Zones() {
  const { zones, cages, reoptimiseZone } = useApp()
  const [busy, setBusy] = useState<string | null>(null)
  const [result, setResult] = useState<{ zoneId: string; util: number } | null>(null)

  async function run(zoneId: string) {
    setBusy(zoneId)
    const util = await reoptimiseZone(zoneId)
    setResult({ zoneId, util })
    setBusy(null)
  }

  return (
    <>
      <PageHeader
        title="Storage Zones"
        desc="Static zones the AGV fleet serves. “Re-optimise” recomputes cage loading for a zone."
      />
      <div className="grid cols-3">
        {zones.map((z) => {
          const zoneCages = cages.filter((c) => c.zoneId === z.id)
          const isCold = z.kind === 'cold-store'
          return (
            <Card key={z.id}>
              <div className="row between">
                <div>
                  <h3 style={{ margin: 0, fontSize: 15 }}>{z.name}</h3>
                  <div className="faint mono" style={{ fontSize: 11.5 }}>{z.id}</div>
                </div>
                <Badge tone={isCold ? 'cyan' : z.kind === 'dispatch' ? 'violet' : 'grey'}>{ZONE_KIND[z.kind]}</Badge>
              </div>

              <div className="row between" style={{ marginTop: 14 }}>
                <div>
                  <div className="kpi-label">Temperature</div>
                  <div className="kpi-value" style={{ fontSize: 20, color: isCold ? 'var(--accent)' : 'var(--text)' }}>
                    {z.temperatureC}°C
                  </div>
                </div>
                <div className="right">
                  <div className="kpi-label">Cages</div>
                  <div className="kpi-value" style={{ fontSize: 20 }}>{zoneCages.length}/{z.capacityCages}</div>
                </div>
              </div>

              <div style={{ marginTop: 12 }}>
                <div className="row between" style={{ fontSize: 12 }}>
                  <span className="muted">Zone fill</span>
                  <span>{pct(z.fillRate, 0)}</span>
                </div>
                <Progress value={z.fillRate} tone={z.fillRate > 0.8 ? 'green' : undefined} />
              </div>

              <div className="row" style={{ marginTop: 12 }}>
                <button className="btn sm" disabled={busy === z.id} onClick={() => run(z.id)}>
                  {busy === z.id ? 'Optimising…' : '↻ Re-optimise'}
                </button>
                {result?.zoneId === z.id && (
                  <Badge tone="green">→ {pct(result.util)} projected</Badge>
                )}
              </div>
            </Card>
          )
        })}
      </div>
      <p className="faint" style={{ fontSize: 12, margin: 0 }}>
        Mock — the real command is POST /api/zones/:id/reoptimise, executed by the Django backend using the
        server-side packing engine.
      </p>
    </>
  )
}
