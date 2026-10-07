import { useApp } from '../store/AppStore'
import { Badge, Card, PageHeader, Progress } from '../components/ui'
import { AGV_STATUS, CAGE_STATE } from '../components/status'
import { kg, pct, volumeLitres } from '../lib/format'

export function Fleet() {
  const { agvs, cages } = useApp()
  const connected = agvs.filter((a) => a.status !== 'offline').length

  return (
    <>
      <PageHeader
        title="Fleet & Cages"
        desc="WHEELTEC S200 platforms and roll cages."
        actions={<Badge tone={connected ? 'green' : 'red'} dot>{connected} units connected</Badge>}
      />

      <div className="stale-warning">
        ⚠ Hardware not connected. AGV telemetry (pose, battery) is disabled until the test map is modelled and the
        S200 ROS 2 bridge is online — the fleet below shows last-known records only.
      </div>

      <div className="grid split">
        <Card title="Warehouse map" hint="pending scenario model">
          <div
            style={{
              height: 300,
              borderRadius: 10,
              border: '1px dashed var(--border)',
              background: 'repeating-linear-gradient(45deg, #0b121a, #0b121a 12px, #0d151e 12px, #0d151e 24px)',
              display: 'grid',
              placeItems: 'center',
              textAlign: 'center',
              padding: 24,
            }}
          >
            <div>
              <div style={{ fontSize: 34, opacity: 0.4 }}>▦</div>
              <p className="muted" style={{ margin: '10px 0 4px' }}>
                No map loaded
              </p>
              <p className="faint" style={{ margin: 0, fontSize: 12.5, maxWidth: 340 }}>
                The test scenario map will be modelled and imported here. AGV positions, zones and paths will render
                on top of the real occupancy map.
              </p>
            </div>
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn sm" disabled>
              ⬆ Import map (pending)
            </button>
            <span className="faint" style={{ fontSize: 12 }}>
              Expected: `map.pgm` + `map.yaml` from the SLAM/nav2 build.
            </span>
          </div>
        </Card>

        <div className="stack">
          {agvs.map((a) => (
            <Card key={a.id}>
              <div className="row between">
                <div className="row">
                  <span className="mono" style={{ fontWeight: 600 }}>{a.id}</span>
                  <Badge tone="violet">{a.model}</Badge>
                  {a.sim && <Badge tone="grey">record</Badge>}
                </div>
                <Badge tone={AGV_STATUS[a.status].tone} dot>
                  {AGV_STATUS[a.status].label}
                </Badge>
              </div>
              <div style={{ marginTop: 10 }}>
                <div className="row between" style={{ fontSize: 12 }}>
                  <span className="muted">Battery (last known)</span>
                  <span>{a.battery.toFixed(0)}%</span>
                </div>
                <Progress value={a.battery / 100} tone={a.battery <= 45 ? 'amber' : 'green'} />
              </div>
              <div className="row" style={{ marginTop: 10, fontSize: 12 }}>
                <span className="faint">pose — no telemetry</span>
                <span className="faint">mission —</span>
              </div>
            </Card>
          ))}
        </div>
      </div>

      <Card title="Roll cages" hint={`${cages.filter((c) => c.state !== 'empty').length} of ${cages.length} loaded`}>
        <table className="data">
          <thead>
            <tr>
              <th>Cage</th>
              <th>State</th>
              <th>Zone</th>
              <th style={{ width: 180 }}>Fill</th>
              <th>Load</th>
              <th>Weight</th>
              <th>Centre of gravity</th>
              <th>Order</th>
            </tr>
          </thead>
          <tbody>
            {cages.map((c) => {
              const binVol = c.dims.w * c.dims.d * c.dims.h
              const fill = c.loadVolume / binVol
              return (
                <tr key={c.id}>
                  <td className="mono">{c.id}</td>
                  <td>
                    <Badge tone={CAGE_STATE[c.state].tone} dot>
                      {CAGE_STATE[c.state].label}
                    </Badge>
                  </td>
                  <td className="muted">{c.zoneId ?? '—'}</td>
                  <td>
                    <Progress value={fill} tone={fill > 0.85 ? 'green' : undefined} />
                    <span className="faint" style={{ fontSize: 11 }}>
                      {pct(fill, 0)} · {volumeLitres(c.loadVolume)}
                    </span>
                  </td>
                  <td className="muted">{kg(c.loadWeight)}</td>
                  <td className="muted">{c.maxWeight} kg max</td>
                  <td className="mono faint" style={{ fontSize: 11.5 }}>
                    {c.centerOfGravity ? `${c.centerOfGravity.x},${c.centerOfGravity.y},${c.centerOfGravity.z}` : '—'}
                  </td>
                  <td className="mono">{c.orderId ?? '—'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </Card>
    </>
  )
}
