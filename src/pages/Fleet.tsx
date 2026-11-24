import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useApp } from '../store/AppStore'
import { vehicleApi, type LiveRobot } from '../services/vehicle'
import { Badge, Card, PageHeader, Progress } from '../components/ui'
import { LidarSweep } from '../components/LidarSweep'
import { AGV_STATUS, CAGE_STATE } from '../components/status'
import { kg, pct, volumeLitres } from '../lib/format'
import { wheeltecBatteryWarning } from '../lib/vehicleStatus'

export function Fleet() {
  const { agvs, cages } = useApp()
  const [live, setLive] = useState<LiveRobot | null>(null)
  const voltageWarning = wheeltecBatteryWarning(live?.battery_v)

  useEffect(() => {
    let alive = true
    let busy = false
    async function poll() {
      if (busy) return
      busy = true
      try {
        const fleet = await vehicleApi.getFleet()
        if (alive) setLive(fleet.robots[0] ?? null)
      } catch {
        if (alive) setLive(null)
      } finally {
        busy = false
      }
    }
    void poll()
    const timer = window.setInterval(poll, 1000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [])

  return (
    <>
      <PageHeader
        title="Fleet & Cages"
        desc="WHEELTEC S200 platforms and roll cages."
        actions={
          <>
            {voltageWarning && (
              <Badge tone="red" dot>{voltageWarning.label}</Badge>
            )}
            <Badge tone={live?.online ? 'green' : 'red'} dot>
              {live ? `${live.id} · ${live.online ? 'online' : 'offline'}` : 'AGV server offline'}
            </Badge>
          </>
        }
      />

      <Card title="AGV control" hint="fused hub · single websocket · robotctl">
        {voltageWarning && (
          <div className={`voltage-alert ${voltageWarning.level}`} role="alert">
            <span className="voltage-led" />
            <span>
              {voltageWarning.level === 'critical' ? 'CHARGE NOW' : 'LOW VOLTAGE'}
              {live?.battery_v != null ? ` · ${live.battery_v.toFixed(1)} V` : ''}
            </span>
            <span className="voltage-alert-note">
              {voltageWarning.level === 'critical'
                ? '22.4 V or below — charge before the next run.'
                : '22.4–22.7 V — plan a charge before the next run.'}
            </span>
          </div>
        )}
        <div className="row between" style={{ gap: 18, flexWrap: 'wrap' }}>
          <div>
            <p style={{ margin: '0 0 4px' }}>Drive the S200 with the hub's AGV console — same site, no second server.</p>
            <p className="faint" style={{ margin: 0, fontSize: 12 }}>
              {live
                ? `${live.id} · ${live.online ? 'online' : 'offline'} · ${live.battery_v != null ? `${live.battery_v.toFixed(1)} V` : 'battery —'} · transport ${live.transport ?? 'none'}${live.nav_mode && live.nav_mode !== 'off' ? ` · nav ${live.nav_mode}` : ''}${live.odom ? ` · odom (${live.odom[0].toFixed(2)}, ${live.odom[1].toFixed(2)}) m` : ''}`
                : 'Waiting for the pull-mode AGV server…'}
            </p>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <Link className="btn primary" to="/drive">🎮 AGV Drive Console</Link>
            <a
              className="btn"
              href="/drive"
              target="_blank"
              rel="noreferrer"
            >
              Console ↗
            </a>
          </div>
        </div>
      </Card>

      <Card title={`Live LiDAR${live ? ` · ${live.id}` : ''}`} hint="GET /api/robot/:id/scan · polar sweep">
        <LidarSweep robotId={live?.id ?? 's2000-1'} />
      </Card>

      <div className="stale-warning">
        The AGV badge in the topbar is LIVE whenever this page's fleet/lidar feeds answer (single fused hub).
        Records below are mock/last-known planning data; live AGV driving is the console above.
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
        <div className="table-scroll"><table className="data">
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
        </div>
      </Card>
    </>
  )
}
