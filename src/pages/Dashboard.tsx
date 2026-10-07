import { Link } from 'react-router-dom'
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useApp } from '../store/AppStore'
import { Badge, Card, KpiCard, PageHeader, Progress } from '../components/ui'
import { AGV_STATUS, MISSION_STATUS, ORDER_STATUS } from '../components/status'
import { clock, pct } from '../lib/format'

const tipStyle = {
  background: '#0b121a',
  border: '1px solid #1e2a38',
  borderRadius: 8,
  color: '#e6edf5',
  fontSize: 12,
}

export function Dashboard() {
  const { orders, cages, agvs, missions, throughput, activities } = useApp()

  const activeOrders = orders.filter((o) => o.status !== 'done').length
  const cagesInUse = cages.filter((c) => c.state !== 'empty').length
  const online = agvs.filter((a) => a.status !== 'offline').length
  const avgUtil = throughput.reduce((acc, p) => acc + p.utilisation, 0) / throughput.length

  const batteryAvg = agvs.reduce((a, b) => a + b.battery, 0) / agvs.length

  return (
    <>
      <PageHeader
        title="Dashboard"
        desc="Live overview of orders, roll-cage utilisation, fleet and missions."
        actions={<Badge tone="cyan" dot>Auto-refresh 1s</Badge>}
      />

      <div className="grid cols-4">
        <KpiCard label="Active orders" value={activeOrders} foot={`${orders.length} total`} tone="cyan" />
        <KpiCard label="Cages in use" value={cagesInUse} foot={`${cages.length} cages in fleet`} tone="violet" />
        <KpiCard label="Fleet online" value={`${online}/${agvs.length}`} foot={`avg battery ${batteryAvg.toFixed(0)}%`} tone="green" />
        <KpiCard label="Avg utilisation" value={pct(avgUtil)} foot="rolling window" tone="cyan" />
      </div>

      <div className="grid split">
        <Card title="Throughput" hint="orders vs roll-cage trips per hour">
          <div style={{ height: 240 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={throughput} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
                <CartesianGrid stroke="#16212d" vertical={false} />
                <XAxis dataKey="t" tick={{ fill: '#5d7186', fontSize: 11 }} stroke="#1e2a38" />
                <YAxis tick={{ fill: '#5d7186', fontSize: 11 }} stroke="#1e2a38" />
                <Tooltip contentStyle={tipStyle} />
                <Line type="monotone" dataKey="orders" name="Orders" stroke="#22d3ee" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="cages" name="Cage trips" stroke="#a78bfa" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card title="Cage space utilisation" hint="share of usable cage volume filled">
          <div style={{ height: 240 }}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={throughput} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
                <defs>
                  <linearGradient id="util" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#34d399" stopOpacity={0.5} />
                    <stop offset="100%" stopColor="#34d399" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="#16212d" vertical={false} />
                <XAxis dataKey="t" tick={{ fill: '#5d7186', fontSize: 11 }} stroke="#1e2a38" />
                <YAxis tickFormatter={(v) => `${Math.round(v * 100)}%`} domain={[0, 1]} tick={{ fill: '#5d7186', fontSize: 11 }} stroke="#1e2a38" />
                <Tooltip contentStyle={tipStyle} formatter={(v: number) => pct(v)} />
                <Area type="monotone" dataKey="utilisation" name="Utilisation" stroke="#34d399" strokeWidth={2} fill="url(#util)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>

      <div className="grid split">
        <Card
          title="Active missions"
          right={<Link className="btn sm ghost" to="/missions">Open console →</Link>}
        >
          <table className="data">
            <thead>
              <tr>
                <th>Mission</th>
                <th>Type</th>
                <th>Order</th>
                <th>AGV</th>
                <th style={{ width: 160 }}>Progress</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {missions.filter((m) => m.status === 'running' || m.status === 'paused' || m.status === 'queued').slice(0, 5).map((m) => (
                <tr key={m.id}>
                  <td className="mono">{m.id}</td>
                  <td className="muted">{m.type}</td>
                  <td className="mono">{m.orderId ?? '—'}</td>
                  <td className="mono">{m.agvId ?? '—'}</td>
                  <td>
                    <Progress value={m.progress} />
                    <span className="faint" style={{ fontSize: 11 }}>{pct(m.progress, 0)}</span>
                  </td>
                  <td>
                    <Badge tone={MISSION_STATUS[m.status].tone} dot>
                      {MISSION_STATUS[m.status].label}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <Card title="Activity feed" hint="operator + system events">
          <div>
            {activities.slice(0, 8).map((a) => (
              <div className="activity" key={a.id}>
                <span className="t">{clock(a.time)}</span>
                <span className={`lv-${a.level}`}>●</span>
                <span>{a.text}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <div className="grid cols-2">
        <Card title="Fleet snapshot" right={<Link className="btn sm ghost" to="/fleet">View fleet →</Link>}>
          <table className="data">
            <thead>
              <tr>
                <th>AGV</th>
                <th>Model</th>
                <th>Status</th>
                <th style={{ width: 140 }}>Battery</th>
              </tr>
            </thead>
            <tbody>
              {agvs.map((a) => (
                <tr key={a.id}>
                  <td className="mono">{a.id}</td>
                  <td className="muted">{a.model}</td>
                  <td>
                    <Badge tone={AGV_STATUS[a.status].tone} dot>
                      {AGV_STATUS[a.status].label}
                    </Badge>
                  </td>
                  <td>
                    <Progress value={a.battery / 100} tone={a.battery <= 45 ? 'amber' : 'green'} />
                    <span className="faint" style={{ fontSize: 11 }}>{a.battery.toFixed(0)}%</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <Card title="Orders requiring attention" right={<Link className="btn sm ghost" to="/orders">View orders →</Link>}>
          <table className="data">
            <thead>
              <tr>
                <th>Order</th>
                <th>Branch</th>
                <th>Priority</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {orders.filter((o) => o.status !== 'done').slice(0, 5).map((o) => (
                <tr key={o.id}>
                  <td className="mono">{o.id}</td>
                  <td className="muted">{o.branch}</td>
                  <td>
                    <Badge tone={o.priority === 'high' ? 'red' : 'grey'}>{o.priority}</Badge>
                  </td>
                  <td>
                    <Badge tone={ORDER_STATUS[o.status].tone} dot>
                      {ORDER_STATUS[o.status].label}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </>
  )
}
