// v4.1 merge: THE single-site drive surface. The old standalone AGV console
// SPA (agv-console/, hub-served at /agv/) is now inlined into this panel as
// <AgvDriveApp/> — teleop, mapping, waypoint slots, routes and the big LiDAR
// sweep, one bundle, zero iframes. (Old /agv/* URLs 302 to /drive hub-side.)
import { PageHeader } from '../components/ui'
import { AgvDriveApp } from './agv/AgvDriveApp'

export function AgvConsole() {
  return (
    <>
      <PageHeader
        title="AGV Drive Console"
        desc="Teleop · mapping · waypoints · routes · LiDAR — live S2000 control, fused into the panel (WASD drive · Space e-stop · E hard stop)."
        actions={
          <a className="btn" href="/drive" target="_blank" rel="noreferrer">
            Open in new tab ↗
          </a>
        }
      />
      <AgvDriveApp />
    </>
  )
}
