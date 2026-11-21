import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useApp } from '../store/AppStore'
import { vehicleApi } from '../services/vehicle'
import { armChip, vehicleChip, type Chip } from '../lib/vehicleStatus'
import { Badge } from './ui'
import type { Role } from '../types'

const NAV = [
  { to: '/', label: 'Dashboard', ico: '▚', end: true },
  { to: '/orders', label: 'Orders', ico: '☰' },
  { to: '/goods', label: 'Goods', ico: '▦' },
  { to: '/packing', label: '3D Packing', ico: '▣' },
  { to: '/missions', label: 'Missions', ico: '➤' },
  { to: '/fleet', label: 'Fleet', ico: '⬢' },
  { to: '/drive', label: 'AGV Drive', ico: '◈' },
  { to: '/zones', label: 'Zones', ico: '▤' },
  { to: '/commands', label: 'Commands', ico: '⌘' },
  { to: '/arm', label: 'Arm Control', ico: '⚙' },
  { to: '/integration', label: 'Integration', ico: '⇄' },
]

export function Layout() {
  const { userName, role, setRole, authenticated } = useApp()
  const [now, setNow] = useState(new Date())

  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), 1000)
    return () => window.clearInterval(t)
  }, [])

  // v3.7 CS-B4: chips are PROBED, never hardcoded. AGV chip = does the
  // fused hub's fleet endpoint answer; bridge chip = its own mock flag.
  // Pure logic + unit test: src/lib/vehicleStatus.ts.
  const [chip, setChip] = useState<Chip>({ label: 'AGV DATA · …', tone: 'amber' })
  const [arm, setArm] = useState<Chip>({ label: 'bridge · …', tone: 'amber' })
  useEffect(() => {
    let alive = true
    const probe = () => {
      vehicleApi
        .getFleet()
        .then((f) => alive && setChip(vehicleChip(!!f && Array.isArray(f.robots))))
        .catch(() => alive && setChip(vehicleChip(false)))
      vehicleApi.getHealth().then((h) => alive && setArm(armChip(h)))
    }
    probe()
    const t = window.setInterval(probe, 6000)
    return () => {
      alive = false
      window.clearInterval(t)
    }
  }, [])

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">PT</div>
          <div>
            <div className="brand-title">PackTug</div>
            <div className="brand-sub">Smart Roll-Cage System</div>
          </div>
        </div>
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <span className="nav-ico">{item.ico}</span>
            {item.label}
          </NavLink>
        ))}
        <div style={{ flex: 1 }} />
        <div className="nav-section">Session</div>
        <div className="row between" style={{ padding: '4px 10px' }}>
          <span className="muted" style={{ fontSize: 12.5 }}>
            {authenticated ? userName : 'Signed out'}
          </span>
          <Badge tone="cyan">{role}</Badge>
        </div>
        <div className="field" style={{ padding: '8px 10px 2px' }}>
          <label>Role</label>
          <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
            <option value="operator">operator</option>
            <option value="supervisor">supervisor</option>
            <option value="maintenance">maintenance</option>
          </select>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <h1>Operator Control Panel</h1>
          <Badge tone={chip.tone} dot>
            {chip.label}
          </Badge>
          <div className="spacer" />
          <span className="faint mono" style={{ fontSize: 12 }}>
            {now.toLocaleTimeString('en-GB')}
          </span>
          <Badge tone={arm.tone} dot>
            {arm.label}
          </Badge>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
