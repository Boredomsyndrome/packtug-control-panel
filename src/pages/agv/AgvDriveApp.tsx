/* AGV drive console — v4.1 merge: the standalone agv-console SPA ported into
   the panel at /drive (was iframe-embedded from the hub-served /agv/).
   Logic is a faithful port of agv-console/src/App.jsx; all styles are scoped
   under .agv-embed (see agv.css) so nothing leaks into the panel theme.
   Safety model unchanged: 10 Hz one-in-flight cmd heartbeat, hub-side
   staleness zeroing, latched e-stop, blur/hide -> stop. */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent } from 'react'
import './agv.css'

/* ---------------- types (shape of /api/robot* frames) ---------------- */

type DriveDir = 'fwd' | 'back' | 'left' | 'right'

interface Applied { vx: number; wz: number }

interface RobotStatus {
  id: string
  online: boolean
  estop?: boolean
  battery_v?: number | null
  max_lin?: number
  max_rot?: number
  cmd_timeout?: number
  scan?: unknown
  applied?: Applied
  watchdog_trips?: number
  seq?: number
  ros_topics?: { scan?: string[]; image?: string[] }
  robot_scan_ok?: boolean
  robot_cam_ok?: boolean
}

interface Waypoint { x: number; y: number; yaw: number }
interface MapInfo { name: string; uploaded?: boolean; bytes?: number }

interface NavBody {
  ok?: boolean
  mode?: string
  nav?: {
    pose?: number[]
    route_total?: number
    route_done?: number
    error?: string
  }
  waypoints?: Record<string, Waypoint>
  maps?: MapInfo[]
}

interface PackedScan {
  ranges: number[]
  amin: number
  ainc: number
  rmax: number
}

/* Approx gauge for the S200's "24V" LiFePO4 pack (voltage-only estimate). */
const BATT_EMPTY = 21.0
const BATT_FULL = 29.0
const BATT_CHARGE_V = 22.4
const BATT_WARN_V = 22.7

const KEYMAP: Record<string, DriveDir> = {
  KeyW: 'fwd', ArrowUp: 'fwd',
  KeyS: 'back', ArrowDown: 'back',
  KeyA: 'left', ArrowLeft: 'left',
  KeyD: 'right', ArrowRight: 'right',
}

/* Human hint for "why is the sensor dead", assembled from the robot's own
   telemetry (robot_*_ok) and its live ROS graph snapshot (ros_topics). */
function sensorHint(status: RobotStatus | null, kind: 'scan' | 'image'): string {
  if (!status) return ''
  const topics = status.ros_topics || {}
  const list = kind === 'scan' ? topics.scan || [] : topics.image || []
  const ok = kind === 'scan' ? status.robot_scan_ok : status.robot_cam_ok
  if (ok) return 'robot sees data but none arrived — uplink problem (check client log/WiFi)'
  if (status.ros_topics && list.length === 0) {
    return kind === 'scan'
      ? 'no LaserScan topic on the robot — start it: ros2 launch turn_on_wheeltec_robot wheeltec_lidar.launch.py'
      : 'no Image topic on the robot graph — launch the camera node, then it self-heals (no client restart needed)'
  }
  return `${kind}: topics visible on robot graph (${list.join(', ')}) but no data — node running? publisher QoS?`
}

async function post<T = Record<string, unknown>>(url: string, body?: unknown, ms = 8000): Promise<T> {
  // bounded like every other request: an un-timeouted POST over a flaky VPN
  // used to pin one of the browser's 6 connections per-host forever, which
  // starved the OTHER requests behind it (the "UI freezes" symptom).
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
    signal: AbortSignal.timeout(ms),
  })
  return res.json().catch(() => ({ ok: res.ok })) as T
}

async function fetchJson<T>(url: string, ms = 4000): Promise<T> {
  const res = await fetch(url, { signal: AbortSignal.timeout(ms) })
  return res.json() as Promise<T>
}

/* ---- polling WITHOUT connection pile-ups --------------------------------
   setInterval + async fn = unbounded overlap: when the hub takes longer to
   answer than the tick period (VPN latency), requests stack until the
   browser's 6-per-host cap is reached and BUTTON POSTs queue behind them.
   pollLoop is single-flight (skips a tick while one is in flight) and backs
   off exponentially while errors persist, so a network stall degrades the
   refresh rate instead of freezing the whole page. */
function pollLoop(fn: () => Promise<void>, ms: number): () => void {
  let stop = false, busy = false, fail = 0, timer: ReturnType<typeof setTimeout> | null = null
  const step = async () => {
    if (stop || busy) return
    busy = true
    try { await fn(); fail = 0 } catch { fail += 1 }
    finally {
      busy = false
      if (!stop) timer = setTimeout(step, Math.min(ms * 2 ** Math.min(fail, 3), ms * 8))
    }
  }
  step()
  return () => { stop = true; if (timer) clearTimeout(timer) }
}

/* Shared, single-flight /nav snapshot — ScanView (point overlay) and
   NavPanel (buttons) used to poll the SAME url each second, doubling the
   pressure; one poller feeds both, plus poke() for instant post-click refresh. */
const navStore: {
  robotId: string | null
  data: NavBody | null
  subs: Set<(d: NavBody) => void>
  loop: null | (() => void)
} = { robotId: null, data: null, subs: new Set(), loop: null }

async function navFetchOnce(robotId: string): Promise<void> {
  try {
    const d = await fetchJson<NavBody>(`/api/robot/${encodeURIComponent(robotId)}/nav`)
    if (d && d.ok) { navStore.data = d; navStore.subs.forEach((f) => f(d)) }
  } catch { /* hub blip: consumers keep the last snapshot */ }
}

function useNavData(robotId: string): NavBody | null {
  const [data, setData] = useState<NavBody | null>(() =>
    (navStore.robotId === robotId ? navStore.data : null))
  useEffect(() => {
    navStore.subs.add(setData)
    if (navStore.robotId !== robotId) {
      navStore.loop?.()
      navStore.robotId = robotId
      navStore.data = null
      navStore.loop = pollLoop(() => navFetchOnce(robotId), 1000)
    } else if (navStore.data) setData(navStore.data)
    return () => { navStore.subs.delete(setData) }
  }, [robotId])
  return data
}

/* ---------------- main console ---------------- */

export function AgvDriveApp() {
  const [robots, setRobots] = useState<RobotStatus[]>([])
  const [sel, setSel] = useState<string | null>(null)   // selected robot id
  const [lin, setLin] = useState(0.3)                   // m/s applied while forward/back held
  const [rot, setRot] = useState(0.5)                   // rad/s applied while turning held
  const [estopLatch, setEstopLatch] = useState(false)

  const keysRef = useRef<Record<DriveDir, boolean>>({ fwd: false, back: false, left: false, right: false })
  const movingRef = useRef(false)
  const selRef = useRef<string | null>(null)
  selRef.current = sel

  const status = robots.find((r) => r.id === sel) || null
  const maxLin = status?.max_lin ?? 0.5
  const maxRot = status?.max_rot ?? 0.7
  const api = (path: string) => `/api/robot/${encodeURIComponent(sel ?? '')}${path}`

  const axisValues = useCallback(() => {
    const k = keysRef.current
    const vx = (k.fwd ? lin : 0) + (k.back ? -lin : 0)
    const wz = (k.left ? rot : 0) + (k.right ? -rot : 0)
    return { vx, wz }
  }, [lin, rot])

  const sendStop = useCallback(async () => {
    if (movingRef.current && selRef.current) {
      movingRef.current = false
      await post(`/api/robot/${encodeURIComponent(selRef.current)}/stop`, {})
    }
  }, [])

  /* Hard stop: clears held buttons/keys AND latched e-stop server-side. */
  const hardStop = useCallback(async () => {
    keysRef.current = { fwd: false, back: false, left: false, right: false }
    movingRef.current = false
    setEstopLatch(false)
    if (selRef.current) {
      await post(`/api/robot/${encodeURIComponent(selRef.current)}/stop`, {})
    }
  }, [])

  const press = useCallback((dir: DriveDir, on: boolean) => {
    keysRef.current[dir] = on
  }, [])

  const doEstop = useCallback(async () => {
    keysRef.current = { fwd: false, back: false, left: false, right: false }
    movingRef.current = false
    if (!selRef.current) return
    await post(`/api/robot/${encodeURIComponent(selRef.current)}/estop`, {})
    setEstopLatch(true)
  }, [])

  /* ---- fleet status polling (1 Hz) ---- */
  useEffect(() => pollLoop(async () => {
    try {
      const d = await fetchJson<{ robots?: RobotStatus[] }>('/api/robot')
      const list = d.robots || []
      setRobots(list)
      setSel((cur) => {
        if (cur && list.some((r) => r.id === cur)) return cur
        return list.length ? list[0].id : null
      })
    } catch {
      setRobots((prev) => prev.map((r) => ({ ...r, online: false })))
      throw new Error('hub unreachable')
    }
  }, 1000), [])

  /* keyboard driving (attached while this page is mounted; reads refs so it
     survives robot switches). WASD/arrows drive, Space = e-stop, E = hard
     stop; typing in inputs never drives. Unmount (route change) tears the
     listeners down — panel pages you navigate away from keep nothing hot. */
  useEffect(() => {
    const typing = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      return !!t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName || '')
    }
    const down = (e: KeyboardEvent) => {
      if (e.repeat) return
      if (typing(e)) return          // autonomy-panel text fields drive nothing
      if (e.code === 'Space') { e.preventDefault(); void doEstop(); return }
      if (e.code === 'KeyE' && !e.ctrlKey && !e.altKey && !e.metaKey) {
        e.preventDefault(); void hardStop(); return
      }
      const dir = KEYMAP[e.code]
      if (dir && !e.ctrlKey && !e.altKey && !e.metaKey) {
        e.preventDefault()
        press(dir, true)
      }
    }
    const up = (e: KeyboardEvent) => {
      const dir = KEYMAP[e.code]
      if (dir) press(dir, false)
    }
    const clearAll = () => {
      keysRef.current = { fwd: false, back: false, left: false, right: false }
      void sendStop()
    }
    const vis = () => { if (document.hidden) clearAll() }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    window.addEventListener('blur', clearAll)
    document.addEventListener('visibilitychange', vis)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
      window.removeEventListener('blur', clearAll)
      document.removeEventListener('visibilitychange', vis)
    }
  }, [press, sendStop, doEstop, hardStop])

  /* ---- 10 Hz command heartbeat while buttons/keys are held ----
     Commands only go to the SELECTED robot; offline robots get nothing
     (their hub-side staleness would zero the cmd anyway). */
  useEffect(() => {
    // one heartbeat POST in flight at a time: an unbounded 10 Hz pile-up on
    // a slow link was the worst connection-hogger on the page. If a cmd POST
    // is still pending the hub-side staleness timer keeps the robot safe;
    // we only ever want the NEWEST cmd, never a replay queue.
    let hbBusy = false
    const id = setInterval(async () => {
      if (!sel || hbBusy) return
      const { vx, wz } = axisValues()
      if (vx !== 0 || wz !== 0) {
        movingRef.current = true
        hbBusy = true
        try { await post(api('/cmd'), { vx, wz }, 1500) } finally { hbBusy = false }
      } else if (movingRef.current) {
        hbBusy = true
        try { await sendStop() } finally { hbBusy = false }
      }
    }, 100)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [axisValues, sendStop, sel])

  const pickRobot = (id: string) => {
    if (id === sel) return
    keysRef.current = { fwd: false, back: false, left: false, right: false }
    if (movingRef.current && sel) {
      movingRef.current = false
      void post(`/api/robot/${encodeURIComponent(sel)}/stop`, {})  // stop the old one
    }
    setSel(id)
  }

  const battV = status?.battery_v
  const battPct = battV == null ? null :
    Math.max(0, Math.min(100, Math.round(((battV - BATT_EMPTY) / (BATT_FULL - BATT_EMPTY)) * 100)))
  const battWarning: 'charge' | 'warn' | null = battV == null ? null :
    battV <= BATT_CHARGE_V ? 'charge' : battV <= BATT_WARN_V ? 'warn' : null

  const connected = !!status?.online

  const presets = [
    { name: '🐢 crawl', lin: 0.15, rot: 0.25 },
    { name: 'walk', lin: 0.30, rot: 0.50 },
    { name: '🐇 max', lin: maxLin, rot: maxRot },
  ]
  const applyPreset = (p: { lin: number; rot: number }) => { setLin(p.lin); setRot(p.rot) }

  return (
    <div className="agv-embed">
      <header className="agv-topbar">
        <div className="agv-fleet">
          {robots.length === 1 && <span className="agv-dim one">1 robot ·</span>}
          {robots.map((r) => (
            <button key={r.id}
                    className={`agv-rchip ${r.id === sel ? 'sel' : ''}`}
                    onClick={() => pickRobot(r.id)}
                    title={`${r.id}: ${r.online ? 'online' : 'offline'}`}>
              <span className={`agv-dot ${r.online ? 'ok' : 'bad'}`} />
              {r.id}
              <small className={r.battery_v != null && r.battery_v <= BATT_WARN_V ? 'low' : ''}>
                {r.battery_v != null ? `${r.battery_v.toFixed(1)}V` : '--'}
              </small>
              {r.estop && <span className="agv-mini-estop">STOP</span>}
            </button>
          ))}
          {robots.length === 0 && <span className="agv-dim">no robots registered</span>}
        </div>
        <div className="agv-badges">
          <span className={`agv-dot ${connected ? 'ok' : 'bad'}`} />
          <span>{sel ? (connected ? `${sel} online` : `${sel} OFFLINE`) : '—'}</span>
          {status?.estop && <span className="agv-estopbadge">E-STOP latched</span>}
          {battWarning && (
            <span className={`agv-battwarn ${battWarning}`} role="alert">
              <span className="agv-battled" />
              {battWarning === 'charge' ? 'CHARGE NOW' : 'LOW VOLTAGE'}
              {battV != null ? ` · ${battV.toFixed(1)} V` : ''}
            </span>
          )}
          <span className="agv-batt">
            {battV != null ? `${battV.toFixed(1)} V` : '-- V'}
            {battPct != null && (
              <span className={`agv-bar ${battWarning ? 'warn' : ''}`}>
                <i style={{ width: `${battPct}%` }} />
              </span>
            )}
          </span>
        </div>
      </header>

      <main className="agv-grid">
        <div className="agv-leftcol">
          <section className="agv-lidar">
            {sel && <ScanView key={sel} robotId={sel} status={status} />}
          </section>
        </div>

        <div className="agv-rightcol">
          <section className="agv-drive">
            <div className="agv-dpad" onContextMenu={(e) => e.preventDefault()}>
              <DriveBtn label="▲" dir="fwd" press={press} disabled={!connected} />
              <div className="agv-dpad-mid">
                <DriveBtn label="◀" dir="left" press={press} disabled={!connected} />
                <div className="agv-dpad-center">hold to move<br />WASD/arrows ok</div>
                <DriveBtn label="▶" dir="right" press={press} disabled={!connected} />
              </div>
              <DriveBtn label="▼" dir="back" press={press} disabled={!connected} />
            </div>

            <div className="agv-row agv-presets">
              {presets.map((p) => (
                <button key={p.name}
                        className={`agv-preset ${Math.abs(lin - p.lin) < 0.001 ? 'on' : ''}`}
                        onClick={() => applyPreset(p)}>
                  {p.name}
                  <small>{p.lin.toFixed(2)} m/s</small>
                </button>
              ))}
            </div>

            <div className="agv-sliders">
              <label>
                Drive speed <b>{lin.toFixed(2)} m/s</b>
                <input type="range" min="0.05" max={maxLin} step="0.05"
                       value={lin} onChange={(e) => setLin(parseFloat(e.target.value))} />
              </label>
              <label>
                Turn speed <b>{rot.toFixed(2)} rad/s</b>
                <input type="range" min="0.05" max={maxRot} step="0.05"
                       value={rot} onChange={(e) => setRot(parseFloat(e.target.value))} />
              </label>
            </div>

            <div className="agv-row">
              <button className="agv-stopbtn" onClick={() => void hardStop()}>⏹ STOP <small>(E)</small></button>
              {estopLatch && (
                <button className="agv-clearbtn" onClick={() => void hardStop()}>✓ CLEAR E-STOP</button>
              )}
            </div>

            <button className="agv-estop" onPointerDown={() => void doEstop()}>
              ■ E-STOP <small>(space)</small>
            </button>

            <p className="agv-hint">
              Robot stops if commands lapse &gt; {status?.cmd_timeout ?? 0.5}s (server) or its
              own link dies (onboard watchdog). Page blur/hide always sends stop.
            </p>
          </section>

          {sel && (
            <section className="agv-navpanel" id="autonomy">
              <NavPanel key={sel} robotId={sel} connected={connected} />
            </section>
          )}
        </div>
      </main>

      <footer className="agv-foot">
        {status?.applied ? (
          <span>
            {status.id} · applied {status.applied.vx.toFixed(2)} m/s {status.applied.wz.toFixed(2)} rad/s ·
            link state {status.applied.vx !== 0 || status.applied.wz !== 0 ? 'driving' : 'idle'} ·
            watchdog trips {status.watchdog_trips ?? 0} · seq {status.seq ?? '—'} ·
            lidar {status.scan ? 'live' : 'off'} ·
            clamps ±{maxLin} m/s ±{maxRot} rad/s
          </span>
        ) : <span>connecting…</span>}
      </footer>
    </div>
  )
}

/* ---------------- LiDAR sweep ---------------- */

interface ScanMeta { points: number; age: number | null; fresh: boolean; err: boolean }

function ScanView({ robotId, status }: { robotId: string; status: RobotStatus | null }) {
  // LiDAR panel: polls /api/robot/<id>/scan (~3 Hz) and draws the newest
  // packed LaserScan. ROS convention: +x forward (up on screen), angles
  // counterclockwise, 0.0 range = no return (left as a gap).
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const scanRef = useRef<PackedScan | null>(null)
  const navData = useNavData(robotId)    // shared single-flight /nav snapshot
  const navRef = useRef<NavBody | null>(null)   // GET /nav body — overlay of saved points
  navRef.current = navData
  const [navTick, setNavTick] = useState(0)
  useEffect(() => setNavTick((n) => n + 1), [navData])
  const [meta, setMeta] = useState<ScanMeta>({ points: 0, age: null, fresh: false, err: false })

  useEffect(() => pollLoop(async () => {
    try {
      const d = await fetchJson<{ scan?: PackedScan; age?: number | null; fresh?: boolean }>(
        `/api/robot/${encodeURIComponent(robotId)}/scan`)
      if (d.scan) scanRef.current = d.scan
      setMeta({ points: d.scan ? d.scan.ranges.length : 0,
                age: d.age ?? null, fresh: !!d.fresh, err: false })
    } catch {
      setMeta((m) => ({ ...m, err: true }))
      throw new Error('scan fetch failed')
    }
  }, 300), [robotId])

  /* Saved waypoints live in the MAP frame. When SLAM/Nav2 reports a pose we
     transform them into the robot frame and paint them on the lidar sweep —
     so 'where is P2 from here' is readable at a glance, even mid-mapping.
     (the /nav request itself is the shared single-flight store in useNavData) */
  useEffect(() => {
    const cv = canvasRef.current
    if (!cv) return
    const draw = () => {
      const dpr = window.devicePixelRatio || 1
      const W = cv.clientWidth, H = cv.clientHeight
      if (!W || !H) return
      if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) {
        cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr)
      }
      const g = cv.getContext('2d')
      if (!g) return
      g.setTransform(dpr, 0, 0, dpr, 0, 0)
      g.clearRect(0, 0, W, H)
      const scan = scanRef.current
      const cx = W / 2, cy = H / 2
      let rmax = scan ? scan.rmax : 8
      if (scan) {
        let m = 0
        for (const r of scan.ranges) if (r > m) m = r
        if (m > 0) rmax = Math.min(scan.rmax, Math.max(m * 1.15, 0.5))
      }
      const scale = (Math.min(W, H) / 2 / rmax) * 0.92
      const stepv = rmax <= 2 ? 0.5 : rmax <= 8 ? 1 : rmax <= 20 ? 5 : 20
      g.strokeStyle = 'rgba(110,160,150,0.18)'
      g.fillStyle = 'rgba(150,190,180,0.45)'
      g.font = '10px ui-monospace, Menlo, monospace'
      for (let r = stepv; r <= rmax + 1e-9; r += stepv) {
        g.beginPath(); g.arc(cx, cy, r * scale, 0, 2 * Math.PI); g.stroke()
        g.fillText(`${r}m`, cx + 3, cy - r * scale - 2)
      }
      g.beginPath(); g.moveTo(cx - 5, cy); g.lineTo(cx + 5, cy)
      g.moveTo(cx, cy - 5); g.lineTo(cx, cy + 5); g.stroke()
      if (scan) {
        g.fillStyle = meta.fresh ? '#5ff0c0' : '#8d8d6f'
        const n = scan.ranges.length
        for (let i = 0; i < n; i++) {
          const r = scan.ranges[i]
          if (r <= 0) continue
          const a = scan.amin + i * scan.ainc
          const x = cx - r * Math.sin(a) * scale     // +x up, +y left
          const y = cy - r * Math.cos(a) * scale
          g.fillRect(x - 1.1, y - 1.1, 2.3, 2.3)
        }
        g.fillStyle = '#ffd05f'
        g.beginPath(); g.moveTo(cx, cy - 7); g.lineTo(cx - 4.5, cy + 4)
        g.lineTo(cx + 4.5, cy + 4); g.closePath(); g.fill()
      }
      const nv = navRef.current
      if (scan && nv && Array.isArray(nv.nav?.pose)) {
        const [p0 = 0, p1 = 0, ph = 0] = nv.nav.pose
        const co = Math.cos(ph), si = Math.sin(ph)
        for (const [n, w] of Object.entries(nv.waypoints || {})) {
          const dx = w.x - p0, dy = w.y - p1
          const fwd = dx * co + dy * si        // ahead of the robot
          const lft = -dx * si + dy * co       // to its left
          if (fwd * fwd + lft * lft > rmax * rmax) continue
          const sx = cx - lft * scale, sy = cy - fwd * scale
          g.beginPath(); g.arc(sx, sy, 4, 0, 2 * Math.PI)
          g.fillStyle = '#4cc2ff'; g.fill()
          g.strokeStyle = '#0b0e14'; g.lineWidth = 1.5; g.stroke()
          g.fillStyle = '#cfe3ff'; g.font = '11px ui-monospace, Menlo, monospace'
          g.fillText(n, sx + 7, sy - 5)
        }
      }
    }
    draw()
    window.addEventListener('resize', draw)
    return () => window.removeEventListener('resize', draw)
  }, [meta, navTick])

  const ageTxt = meta.age == null ? '—' : `${Math.round(meta.age * 1000)} ms`
  return (
    <>
      <canvas ref={canvasRef} />
      <span className={`agv-camtag ${meta.fresh ? '' : 'stale'}`}>
        {meta.err ? 'lidar: server unreachable'
          : meta.fresh ? `◉ lidar ${meta.points} rays · ${ageTxt}`
          : `lidar idle · last ${ageTxt}`}
      </span>
      {!meta.fresh && status?.online && (
        <div className="agv-lidhint">{sensorHint(status, 'scan')}</div>
      )}
    </>
  )
}

/* ---------------- autonomy panel (docs/NAV.md) ----------------
   Polls GET /api/robot/<id>/nav {mode, nav, waypoints, maps}; every button is
   one validated POST action. Nothing here moves the robot except START ROUTE
   (server-side the /cmd route also gets a 409 so teleop can't fight Nav2). */
const NAV_LABEL: Record<string, string> = { off: 'idle', mapping: 'MAPPING', localizing: 'nav ready', route: 'ROUTE ▶' }
/* Waypoints are an UNBOUNDED set of auto-named p1..pN stamps (the hub only
   validates [A-Za-z0-9_-]{1,32}, never a count). Display sorts p-stamps
   numerically (p2 before p10) and any odd names after them alphabetically. */
const WP_STAMP = /^p(\d+)$/
function wpSort(a: string, b: string): number {
  const na = WP_STAMP.exec(a)
  const nb = WP_STAMP.exec(b)
  if (na && nb) return Number(na[1]) - Number(nb[1])
  if (na) return -1
  if (nb) return 1
  return a.localeCompare(b)
}
function nextWpName(wps: Record<string, Waypoint>): string {
  let max = 0
  for (const n of Object.keys(wps)) {
    const m = WP_STAMP.exec(n)
    if (m) max = Math.max(max, Number(m[1]))
  }
  return `p${max + 1}`
}

function NavPanel({ robotId, connected }: { robotId: string; connected: boolean }) {
  const data = useNavData(robotId)       // shared single-flight /nav body
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [mapName, setMapName] = useState('room1')
  const [showMap, setShowMap] = useState('')
  const [route, setRoute] = useState<string[]>([])

  const navUrl = `/api/robot/${encodeURIComponent(robotId)}/nav`
  const mode = data?.mode || 'off'
  const navs: NonNullable<NavBody['nav']> = data?.nav || {}
  const wps = data?.waypoints || {}
  const maps = data?.maps || []

  /* keep the preview selection pointing at a real map */
  useEffect(() => {
    if (!maps.length) { if (showMap) setShowMap(''); return }
    if (!maps.some((m) => m.name === showMap)) setShowMap(maps[0].name)
  }, [maps, showMap])

  const act = async (body: Record<string, unknown>) => {
    setBusy(true); setErr('')
    try {
      const d = await post<{ ok?: boolean; error?: string; errors?: Record<string, unknown> }>(navUrl, body)
      if (!d.ok) setErr(d.error || (d.errors && JSON.stringify(d.errors)) || 'rejected')
    } catch {
      // POST timed out — the action may STILL have landed hub-side (the
      // hub answers in ms; the answer just didn't get back). Say so instead
      // of implying failure: users double-clicked on 'network' and stacked
      // a dozen identical actions that all executed.
      setErr('no answer in 8 s — the action may still have landed; check the mode chip (wait 5 s) before clicking again')
    } finally {
      setBusy(false)
      void navFetchOnce(robotId)   // refresh NOW instead of waiting a poll tick
    }
  }

  const live = connected && !busy
  const wpNames = Object.keys(wps).sort(wpSort)
  const wpList = wpNames.map((n) => [n, wps[n]] as const)
  const nextName = nextWpName(wps)
  const navActive = mode === 'mapping' || mode === 'localizing' || mode === 'route'

  return (
    <>
      <div className="agv-navhead">
        <b>🧭 Autonomy</b>
        <span className={`agv-navmode ${mode}`}>{NAV_LABEL[mode] || mode}</span>
        {navs.pose && <span className="agv-dim agv-navpose">
          x {navs.pose[0].toFixed(2)} · y {navs.pose[1].toFixed(2)}
        </span>}
        {navs.route_total ? <span className="agv-dim">{navs.route_done ?? 0}/{navs.route_total} pts</span> : null}
      </div>
      {navs.error && <div className="agv-naverr">robot: {navs.error}</div>}
      {err && <div className="agv-naverr">⚠ {err}</div>}

      <div className="agv-navmap">
        {maps.length ? (
          <div className="agv-row">
            <select value={showMap} onChange={(e) => setShowMap(e.target.value)}>
              {maps.map((m) => (
                <option key={m.name} value={m.name}>
                  {m.name}{m.uploaded === false
                    ? ' (robot only — no preview)'
                    : ` · ${Math.round((m.bytes ?? 0) / 1024)} KB`}
                </option>
              ))}
            </select>
            <button className="agv-minibtn" disabled={!live} title="delete this map"
                    onClick={() => void act({ action: 'delete_map', map: showMap })}>✕</button>
          </div>
        ) : <span className="agv-dim">no saved maps yet — press 🗺, teleop around the room, then 💾</span>}
        {showMap && (
          <MapCanvas robotId={robotId} map={showMap} wps={wps} pose={navs.pose}
                     route={route}
                     onPick={(n) => setRoute((r) => (r.includes(n) ? r : [...r, n]))} />
        )}
      </div>

      <div className="agv-row">
        <button className={`agv-navbtn ${mode === 'mapping' ? 'on' : ''}`}
                disabled={!live || mode === 'route'}
                onClick={() => void act({ action: 'map_on' })}>🗺 Map</button>
        <button className="agv-navbtn" disabled={!live || mode !== 'mapping'}
                onClick={() => void act({ action: 'map_off' })}>map ⏹</button>
        <button className="agv-navbtn" disabled={!live || mode === 'off' || mode === 'route'}
                title="stop the navigation stack"
                onClick={() => void act({ action: 'nav_off' })}>⏹ nav stop</button>
      </div>
      <div className="agv-row">
        <input value={mapName} maxLength={32} placeholder="map name"
               disabled={!live || mode === 'route'}
               onChange={(e) => setMapName(e.target.value.replace(/[^A-Za-z0-9_-]/g, ''))} />
        <button className="agv-navbtn" disabled={!live || !navActive}
                title="stop SLAM, save it as the named map (needs SLAM/nav running)"
                onClick={() => void act({ action: 'map_save', name: mapName || 'room1' })}>💾 save</button>
        <button className="agv-navbtn" disabled={!live || !maps.length || mode === 'route'}
                title="start Nav2 localization on the selected map"
                onClick={() => void act({ action: 'nav_on', map: showMap || maps[0]?.name })}>🎯 nav</button>
      </div>
      <div className="agv-row agv-wpslotrow">
        <button className="agv-navbtn save" disabled={!live || !navActive}
                title={`store the robot's current pose as ${nextName}`}
                onClick={() => void act({ action: 'record', name: nextName })}>📍 save point</button>
        {wpNames.length > 1 && (
          <button className="agv-navbtn" disabled={!live || mode === 'route'}
                  title="queue ALL saved points in p1→pN order (path replay)"
                  onClick={() => setRoute([...wpNames])}>⇓ queue all</button>
        )}
      </div>
      <div className="agv-wpreadout">
        <span><b>{wpNames.length}</b> pts saved · next: <b>{nextName}</b></span>
      </div>

      <div className="agv-navwps">
        {wpList.length === 0 &&
          <span className="agv-dim">no points yet — park the robot somewhere and hit 📍</span>}
        {wpList.map(([n, w]) => (
          <div key={n} className="agv-wprow">
            <button className="agv-wpadd" disabled={!live}
                    title={route.includes(n) ? 'in route' : 'add to route'}
                    onClick={() => setRoute((r) => (r.includes(n) ? r : [...r, n]))}>
              {route.includes(n) ? `#${route.indexOf(n) + 1}` : '+'}
            </button>
            <span className="agv-wpname">{n}</span>
            <span className="agv-dim agv-wpxy">{w.x.toFixed(2)}, {w.y.toFixed(2)} · {w.yaw.toFixed(1)} rad</span>
            <button className="agv-minibtn" disabled={!live || mode !== 'localizing'}
                    title="re-localize here (no motion)"
                    onClick={() => void act({ action: 'align', waypoints: [n] })}>⌖</button>
            <button className="agv-minibtn" disabled={!live} title="delete point"
                    onClick={() => {
                      void act({ action: 'delete_waypoint', waypoints: [n] })
                      setRoute((r) => r.filter((x) => x !== n))
                    }}>✕</button>
          </div>
        ))}
        {Object.keys(wps).length > 0 && (
          <div className="agv-wprow">
            <span className="agv-dim" />
            <button className="agv-minibtn" disabled={!live}
                    title="delete ALL saved waypoints (the whole drawn path)"
                    onClick={() => {
                      if (window.confirm('Delete ALL saved waypoints (the whole path)?')) {
                        void act({ action: 'clear_waypoints' })
                        setRoute([])
                      }
                    }}>⌫ clear path</button>
          </div>
        )}
      </div>

      {route.length > 0 && (
        <div className="agv-navroute">
          {route.map((n, i) => (
            <button key={n} className="agv-wpchip" title="remove from route"
                    onClick={() => setRoute((r) => r.filter((x) => x !== n))}>
              {i + 1}·{n}
            </button>
          ))}
        </div>
      )}

      <div className="agv-row">
        <button className="agv-routebtn"
                disabled={!live || route.length === 0}
                title="drives the route; starts Nav2 on the selected map automatically if the stack is idle"
                onClick={() => void act({ action: 'route', waypoints: route })}>
          ▶ START ROUTE
        </button>
        {mode === 'route' ? (
          <button className="agv-cancelbtn" onClick={() => void act({ action: 'nav_cancel' })}>⏹ cancel</button>
        ) : route.length > 0 && (
          <button className="agv-minibtn" onClick={() => setRoute([])}>clear</button>
        )}
      </div>
      <p className="agv-hint">🗺 teleop while SLAM builds the map → 💾 save → 🎯 localize → park anywhere,
        hit 📍 save point at every stop (auto-names p1, p2, p3 … no limit) → queue points (or ⇓ queue all) →
        START ROUTE. Nothing drives until you press it; E-STOP and the link watchdog always win.</p>
    </>
  )
}

/* ---------------- saved-map canvas ---------------- */

interface MapImage { img: ImageData; W: number; H: number; ox: number; oy: number; res: number }

function MapCanvas({ robotId, map, wps, pose, route, onPick }: {
  robotId: string
  map: string
  wps: Record<string, Waypoint>
  pose?: number[]
  route: string[]
  onPick: (n: string) => void
}) {
  // Draws the saved .pgm (dark-theme LUT) + waypoints + live pose. Fetched
  // once per (robot, map); overlays redraw with each 1 Hz nav poll.
  const base = `/api/robot/${encodeURIComponent(robotId)}/maps/${encodeURIComponent(map)}`
  const cvRef = useRef<HTMLCanvasElement | null>(null)
  const imgRef = useRef<MapImage | null>(null)
  const [ok, setOk] = useState(0)

  useEffect(() => {
    let stop = false
    imgRef.current = null
    ;(async () => {
      try {
        const [pgmRes, yamlRes] = await Promise.all([
          fetch(`${base}.pgm`), fetch(`${base}.yaml`)])
        if (!pgmRes.ok || stop) return
        const buf = new Uint8Array(await pgmRes.arrayBuffer())
        const yaml = yamlRes.ok ? await yamlRes.text() : ''
        const m = parsePgm(buf)
        if (!m || stop) return
        const resM = /resolution:\s*([0-9.eE+-]+)/.exec(yaml)
        const oM = /origin:\s*\[\s*([0-9.eE+-]+)\s*,\s*([0-9.eE+-]+)/.exec(yaml)
        // occupancy semantics: 0 = wall, 254 = free, 205 = unknown; the dark
        // theme inverts so walls read bright on the panel.
        const data = new Uint8ClampedArray(m.W * m.H * 4)
        for (let i = 0; i < m.W * m.H; i++) {
          const v = m.data[i]
          let r, g, b
          if (v <= 100) { r = 0xd8; g = 0xe6; b = 0xff }
          else if (v >= 250) { r = 0x14; g = 0x19; b = 0x23 }
          else if (v >= 170 && v <= 235) { r = 0x20; g = 0x27; b = 0x34 }
          else { const t = (v - 100) / 150; r = 0x14 + t * 12; g = 0x19 + t * 14; b = 0x23 + t * 19 }
          data[i * 4] = r; data[i * 4 + 1] = g; data[i * 4 + 2] = b; data[i * 4 + 3] = 255
        }
        imgRef.current = {
          img: new ImageData(data, m.W, m.H), W: m.W, H: m.H,
          ox: oM ? parseFloat(oM[1]) : 0, oy: oM ? parseFloat(oM[2]) : 0,
          res: resM ? parseFloat(resM[1]) : 0.05,
        }
        setOk((n) => n + 1)
      } catch { /* keep last frame; stale maps age out on their own */ }
    })()
    return () => { stop = true }
  }, [base])

  useEffect(() => {
    const cv = cvRef.current, it = imgRef.current
    if (!cv || !it) return undefined
    const draw = () => {
      const dpr = window.devicePixelRatio || 1
      const W = cv.clientWidth, H = cv.clientHeight
      if (!W || !H) return
      cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr)
      const g = cv.getContext('2d')
      if (!g) return
      g.setTransform(dpr, 0, 0, dpr, 0, 0)
      g.fillStyle = '#0d1017'; g.fillRect(0, 0, W, H)
      const s = Math.min(W / it.W, H / it.H)
      const dx = (W - it.W * s) / 2, dy = (H - it.H * s) / 2
      const off = document.createElement('canvas')
      off.width = it.W; off.height = it.H
      const og = off.getContext('2d')
      if (!og) return
      og.putImageData(it.img, 0, 0)
      g.imageSmoothingEnabled = false
      g.drawImage(off, dx, dy, it.W * s, it.H * s)
      const wx = (x: number) => dx + ((x - it.ox) / it.res) * s
      const wy = (y: number) => dy + (it.H - (y - it.oy) / it.res) * s   // pgm row 0 = north
      for (const [n, w] of Object.entries(wps)) {
        g.beginPath(); g.arc(wx(w.x), wy(w.y), 4.5, 0, Math.PI * 2)
        g.fillStyle = route.includes(n) ? '#ffd05f' : '#4cc2ff'
        g.fill()
        g.strokeStyle = '#0b0e14'; g.lineWidth = 1.5; g.stroke()
        g.fillStyle = '#cfe3ff'; g.font = '11px ui-monospace, Menlo, monospace'
        g.fillText(n, wx(w.x) + 7, wy(w.y) - 5)
      }
      if (pose) {
        g.save(); g.translate(wx(pose[0]), wy(pose[1])); g.rotate(-(pose[2] || 0))
        g.beginPath(); g.moveTo(9, 0); g.lineTo(-6, -5.5); g.lineTo(-6, 5.5)
        g.closePath(); g.fillStyle = '#3ddc84'; g.fill(); g.restore()
      }
    }
    draw()
    window.addEventListener('resize', draw)
    return () => window.removeEventListener('resize', draw)
  }, [ok, wps, pose, route])

  const click = (e: ReactMouseEvent<HTMLCanvasElement>) => {
    const it = imgRef.current, cv = cvRef.current
    if (!it || !cv) return
    const rect = cv.getBoundingClientRect()
    const s = Math.min(rect.width / it.W, rect.height / it.H)
    const dx = (rect.width - it.W * s) / 2, dy = (rect.height - it.H * s) / 2
    const px = e.clientX - rect.left, py = e.clientY - rect.top
    let best: string | null = null, bd = 18 * 18
    for (const [n, w] of Object.entries(wps)) {
      const ddx = dx + ((w.x - it.ox) / it.res) * s - px
      const ddy = dy + (it.H - (w.y - it.oy) / it.res) * s - py
      const d = ddx * ddx + ddy * ddy
      if (d < bd) { bd = d; best = n }
    }
    if (best) onPick(best)
  }

  return <canvas ref={cvRef} className="agv-navcanvas" onClick={click}
                 title="click a point to add it to the route" />
}

function parsePgm(u8: Uint8Array): { W: number; H: number; data: Uint8Array } | null {
  // Minimal P5 (binary PGM) reader: magic, optional comments, W H maxval,
  // one whitespace byte, then W*H gray bytes (map_server/slam_toolbox style).
  let i = 0
  const tok = (): string | null => {
    while (i < u8.length) {
      const c = u8[i]
      if (c === 0x23) { while (i < u8.length && u8[i++] !== 0x0a) { /* skip comment */ } continue }
      if (c <= 0x20) { i++; continue }
      let s = ''
      while (i < u8.length && u8[i] > 0x20) s += String.fromCharCode(u8[i++])
      return s
    }
    return null
  }
  if (tok() !== 'P5') return null
  const wt = tok(), ht = tok(), mt = tok()
  if (!wt || !ht || !mt) return null
  const W = parseInt(wt, 10)
  const H = parseInt(ht, 10)
  const maxv = parseInt(mt, 10)
  if (!W || !H || !(maxv > 0) || maxv > 255) return null
  i++   // exactly one whitespace byte after maxval per the PGM spec
  if (u8.length < i + W * H) return null
  return { W, H, data: u8.subarray(i, i + W * H) }
}

function DriveBtn({ label, dir, press, disabled }: {
  label: string
  dir: DriveDir
  press: (dir: DriveDir, on: boolean) => void
  disabled: boolean
}) {
  const [active, setActive] = useState(false)
  const on = (e: ReactPointerEvent) => {
    e.preventDefault()
    if (disabled) return
    setActive(true); press(dir, true)
  }
  const off = () => { setActive(false); press(dir, false) }
  return (
    <button
      className={`agv-dbtn ${active ? 'active' : ''} ${disabled ? 'off' : ''}`}
      disabled={disabled}
      onPointerDown={on} onPointerUp={off} onPointerLeave={off}
      onPointerCancel={off}>
      {label}
    </button>
  )
}
