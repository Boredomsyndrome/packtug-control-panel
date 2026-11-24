import { useApp } from '../store/AppStore'
import { Badge, Card, Empty, PageHeader, Progress } from '../components/ui'
import { MISSION_STATUS, MISSION_TYPE } from '../components/status'
import { clock, duration, pct } from '../lib/format'
import type { Mission } from '../types'

function StepDot({ status }: { status: string }) {
  const color =
    status === 'done' ? 'var(--green)' : status === 'running' ? 'var(--accent)' : status === 'failed' ? 'var(--red)' : 'var(--text-faint)'
  return <span style={{ color, fontSize: 12 }}>{status === 'done' ? '✓' : status === 'running' ? '◐' : status === 'failed' ? '✕' : '○'}</span>
}

function MissionCard({ m, onControl }: { m: Mission; onControl: (id: string, a: 'pause' | 'resume' | 'abort') => void }) {
  const active = m.status === 'running' || m.status === 'paused'
  return (
    <Card>
      <div className="row between">
        <div className="row">
          <span className="mono" style={{ fontWeight: 600 }}>{m.id}</span>
          <Badge tone="violet">{MISSION_TYPE[m.type]}</Badge>
          <Badge tone={MISSION_STATUS[m.status].tone} dot>
            {MISSION_STATUS[m.status].label}
          </Badge>
        </div>
        <div className="row">
          {m.status === 'running' && (
            <button className="btn sm" onClick={() => onControl(m.id, 'pause')}>
              ⏸ Pause
            </button>
          )}
          {m.status === 'paused' && (
            <button className="btn sm primary" onClick={() => onControl(m.id, 'resume')}>
              ▶ Resume
            </button>
          )}
          {active && (
            <button className="btn sm danger" onClick={() => onControl(m.id, 'abort')}>
              ✕ Abort
            </button>
          )}
        </div>
      </div>

      <div className="row" style={{ marginTop: 10, fontSize: 12.5 }}>
        <span className="muted">Order <span className="mono">{m.orderId ?? '—'}</span></span>
        <span className="muted">AGV <span className="mono">{m.agvId ?? '—'}</span></span>
        <span className="muted">Cage <span className="mono">{m.cageId ?? '—'}</span></span>
        <span className="faint">ETA {duration(m.etaSec)}</span>
        <span className="faint">started {clock(m.startedAt)}</span>
      </div>

      <div style={{ margin: '12px 0' }}>
        <Progress value={m.progress} tone={m.status === 'paused' ? 'amber' : undefined} />
        <span className="faint" style={{ fontSize: 11 }}>{pct(m.progress, 0)}</span>
      </div>

      <div className="stack" style={{ gap: 6 }}>
        {m.steps.map((s) => (
          <div className="row" key={s.id} style={{ fontSize: 12.5, gap: 8 }}>
            <StepDot status={s.status} />
            <span style={{ color: s.status === 'running' ? 'var(--text)' : 'var(--text-dim)' }}>{s.label}</span>
          </div>
        ))}
      </div>
    </Card>
  )
}

export function Missions() {
  const { missions, controlMission } = useApp()
  const active = missions.filter((m) => m.status === 'running' || m.status === 'paused' || m.status === 'queued')
  const closed = missions.filter((m) => m.status === 'done' || m.status === 'failed')

  return (
    <>
      <PageHeader title="Missions" desc="Picking, packing, towing and charging missions. Simulated until the S200 telemetry bridge is online." />
      <div className="grid cols-2">
        {active.map((m) => (
          <MissionCard key={m.id} m={m} onControl={controlMission} />
        ))}
      </div>
      {active.length === 0 && (
        <Card>
          <Empty>No active missions. Dispatch an order to create one.</Empty>
        </Card>
      )}

      <Card title="History">
        {closed.length === 0 ? (
          <Empty>No completed missions yet.</Empty>
        ) : (
          <div className="table-scroll"><table className="data">
            <thead>
              <tr>
                <th>Mission</th>
                <th>Type</th>
                <th>Order</th>
                <th>AGV</th>
                <th>Started</th>
                <th>Ended</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {closed.map((m) => (
                <tr key={m.id}>
                  <td className="mono">{m.id}</td>
                  <td className="muted">{MISSION_TYPE[m.type]}</td>
                  <td className="mono">{m.orderId ?? '—'}</td>
                  <td className="mono">{m.agvId ?? '—'}</td>
                  <td className="faint">{clock(m.startedAt)}</td>
                  <td className="faint">{clock(m.endedAt)}</td>
                  <td>
                    <Badge tone={MISSION_STATUS[m.status].tone} dot>
                      {MISSION_STATUS[m.status].label}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )}
      </Card>
    </>
  )
}
