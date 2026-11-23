// v3.7 (merge): THE single-site drive surface. The AGV console SPA
// (agv-console/, hub-served at /agv/) embeds in-place: teleop, mapping,
// waypoint slots, routes and the big LiDAR sweep — all driven from inside
// the panel, one origin, no second port, no login.
import { Card, PageHeader } from '../components/ui'

export function AgvConsole() {
  return (
    <>
      <PageHeader
        title="AGV Drive Console"
        desc="Teleop · mapping · waypoints · routes · LiDAR — live S2000 control embedded from the fused hub."
      />
      <Card>
        <iframe
          title="AGV console"
          src="/agv/"
          style={{
            width: '100%',
            height: 'calc(100vh - 210px)',
            minHeight: 520,
            border: '1px solid var(--border)',
            borderRadius: 10,
            background: '#0b0e14',
          }}
        />
        <p className="faint" style={{ margin: '10px 0 0', fontSize: 12 }}>
          Fullscreen / separate window: <a href="/agv/" target="_blank" rel="noreferrer">/agv/ ↗</a>.
          Driving is safety-gated server-side (clamps, 0.5 s staleness-zero, estop latch).
        </p>
      </Card>
    </>
  )
}
