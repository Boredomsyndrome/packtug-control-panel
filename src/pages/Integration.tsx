import { Fragment, useEffect, useState } from 'react'
import { api, type IntegrationStatus } from '../services/api'
import { ENDPOINTS } from '../services/endpoints'
import { Badge, Card, PageHeader } from '../components/ui'

const ROS_TOPICS = [
  { name: '/cmd_vel', dir: 'publish', desc: 'Velocity command to the base (twist)' },
  { name: '/odom', dir: 'subscribe', desc: 'Wheel odometry from the S200 controller' },
  { name: '/battery_state', dir: 'subscribe', desc: 'Battery voltage / percentage' },
  { name: '/imu/data_raw', dir: 'subscribe', desc: 'ICM20948 nine-axis IMU' },
  { name: '/scan', dir: 'subscribe', desc: '2D lidar scan (N10P / M10P)' },
  { name: '/amcl_pose', dir: 'subscribe', desc: 'AMCL localization — nav plane pose & waypoint frame' },
  { name: '/map', dir: 'subscribe', desc: 'Occupancy grid served by vendor map_server' },
  { name: '/follow_waypoints', dir: 'action', desc: 'NavigateToPose action list — routes (up to 256 pts) drive nav2_waypoint_follower' },
  { name: '/mission/command', dir: 'publish', desc: 'High-level routing / task commands (planned)' },
  { name: '/mission/feedback', dir: 'subscribe', desc: 'Execution progress feedback (planned)' },
]

export function Integration() {
  const [status, setStatus] = useState<IntegrationStatus | null>(null)

  useEffect(() => {
    api.getIntegrationStatus().then(setStatus)
  }, [])

  return (
    <>
      <PageHeader
        title="Integration"
        desc="Live and planned interfaces between this panel, the pull-mode AGV server, the arm bridge and the ROS 2 stack."
      />

      <div className="grid cols-4">
        <Card>
          <div className="kpi-label">Backend</div>
          <div style={{ marginTop: 6 }}>
            <Badge tone={status?.backend === 'online' ? 'green' : 'amber'} dot>
              {status?.backend ?? '…'}
            </Badge>
          </div>
          <div className="kpi-foot mono">{status?.apiBase ?? '/api'}</div>
        </Card>
        <Card>
          <div className="kpi-label">ROS 2 bridge</div>
          <div style={{ marginTop: 6 }}>
            <Badge tone="red" dot>{status?.rosBridge ?? '…'}</Badge>
          </div>
          <div className="kpi-foot">Jetson Orin ↔ backend</div>
        </Card>
        <Card>
          <div className="kpi-label">S200 hardware</div>
          <div style={{ marginTop: 6 }}>
            <Badge tone="red" dot>{status?.hardwareConnected ? 'connected' : 'not connected'}</Badge>
          </div>
          <div className="kpi-foot">WHEELTEC S200 · ROS 2 Humble</div>
        </Card>
        <Card>
          <div className="kpi-label">Mode</div>
          <div style={{ marginTop: 6 }}>
            <Badge tone="cyan">{status?.mockMode ? 'MOCK' : 'LIVE'}</Badge>
          </div>
          <div className="kpi-foot">VITE_MOCK / VITE_API_BASE</div>
        </Card>
      </div>

      <Card title="REST endpoints" hint="One origin (:8090) — robot plane + in-process arm/vision bridge are live; only the panel business rows are mock">
        <div className="table-scroll"><table className="data">
          <thead>
            <tr>
              <th style={{ width: 80 }}>Method</th>
              <th>Path</th>
              <th>Description</th>
              <th style={{ width: 150 }}>Status</th>
            </tr>
          </thead>
          <tbody>
            {[...new Set(ENDPOINTS.map((e) => e.group))].map((group) => (
              <Fragment key={group}>
                <tr>
                  <td colSpan={4} style={{ paddingTop: 16, fontWeight: 700, fontSize: 11,
                                          letterSpacing: 0.8, textTransform: 'uppercase', opacity: 0.75 }}>
                    {group}
                  </td>
                </tr>
                {ENDPOINTS.filter((e) => e.group === group).map((e) => (
                  <tr key={`${e.method} ${e.path}`}>
                    <td>
                      <span className="tag">{e.method}</span>
                    </td>
                    <td className="mono">{e.path}</td>
                    <td className="muted">{e.description}</td>
                    <td>
                      <Badge
                        tone={
                          e.status === 'implemented-live' ? 'green' : e.status === 'implemented-mock' ? 'amber' : 'grey'
                        }
                      >
                        {e.status}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </Fragment>
            ))}
          </tbody>
        </table>
        </div>
      </Card>

      <div className="grid split">
        <Card title="ROS 2 topics" hint="wheeltec_ros2 / turn_on_wheeltec_robot stack">
          <div className="table-scroll"><table className="data">
            <thead>
              <tr>
                <th>Topic</th>
                <th>Direction</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              {ROS_TOPICS.map((t) => (
                <tr key={t.name}>
                  <td className="tag">{t.name}</td>
                  <td>
                    <Badge tone={t.dir === 'publish' ? 'cyan' : 'violet'}>{t.dir}</Badge>
                  </td>
                  <td className="muted">{t.desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        </Card>

        <Card title="Robot bringup" hint="on the S200 (ROS 2 Humble)">
          <pre
            className="mono"
            style={{ fontSize: 12, background: '#0b121a', border: '1px solid var(--border)', borderRadius: 8, padding: 12, overflow: 'auto' }}
          >{`# base + sensors
ros2 launch turn_on_wheeltec_robot \\
  turn_on_wheeltec_robot.launch.py
ros2 launch turn_on_wheeltec_robot \\
  wheeltec_sensors.launch.py

# navigation
ros2 launch wheeltec_nav2 wheeltec_nav2.launch.py

# verify
ros2 topic echo /battery_state
ros2 topic echo /odom`}</pre>
          <div className="stack" style={{ marginTop: 12, fontSize: 12.5 }}>
            <div className="row between">
              <span className="muted">SSH user</span>
              <span className="mono">wheeltec</span>
            </div>
            <div className="row between">
              <span className="muted">Password</span>
              <span className="faint">team vault only</span>
            </div>
            <div className="row between">
              <span className="muted">Robot IP</span>
              <span className="mono">192.168.0.100</span>
            </div>
            <div className="row between">
              <span className="muted">Hotspot</span>
              <span className="mono">WHEELTEC_ORINNANO_HUMBLE</span>
            </div>
          </div>
        </Card>
      </div>

      <div className="stale-warning">
        ⚠ AGV online/battery status is live from the pull-mode server; cages, missions and panel orders are
        still simulated. The Ned1 arm channel is live when the bridge is running; open{' '}
        <span className="mono">/arm</span> to connect.
      </div>
    </>
  )
}
