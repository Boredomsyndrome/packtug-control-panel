import React, { useEffect, useRef, useState, useCallback } from 'react'

// Approx gauge for the S200's "24V" LiFePO4 pack (voltage-only estimate).
const BATT_EMPTY = 21.0
const BATT_FULL = 29.0

const KEYMAP = {
  KeyW: 'fwd', ArrowUp: 'fwd',
  KeyS: 'back', ArrowDown: 'back',
  KeyA: 'left', ArrowLeft: 'left',
  KeyD: 'right', ArrowRight: 'right',
}

// Human hint for "why is the sensor dead", assembled from the robot's own
// telemetry (robot_*_ok) and its live ROS graph snapshot (ros_topics).
function sensorHint(status, kind) {
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
  return `${kind}: topics visible on robot (${list.join(', ')}) but no data — node running? publisher QoS?`
}

async function post(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  })
  return res.json().catch(() => ({ ok: res.ok }))
}

export default function App() {
  const [robots, setRobots] = useState([])
  const [sel, setSel] = useState(null)          // selected robot id
  const [lin, setLin] = useState(0.3)           // m/s applied while forward/back held
  const [rot, setRot] = useState(0.5)           // rad/s applied while turning held
  const [estopLatch, setEstopLatch] = useState(false)
  const [camKey, setCamKey] = useState(0)

  const keysRef = useRef({ fwd: false, back: false, left: false, right: false })
  const movingRef = useRef(false)
  const selRef = useRef(null)
  selRef.current = sel

  const status = robots.find((r) => r.id === sel) || null
  const maxLin = status?.max_lin ?? 0.5
  const maxRot = status?.max_rot ?? 0.7
  const api = (path) => `/api/robot/${encodeURIComponent(sel)}${path}`

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

  // Hard stop: clears held buttons/keys AND latched e-stop server-side.
  const hardStop = useCallback(async () => {
    keysRef.current = { fwd: false, back: false, left: false, right: false }
    movingRef.current = false
    setEstopLatch(false)
    if (selRef.current) {
      await post(`/api/robot/${encodeURIComponent(selRef.current)}/stop`, {})
    }
  }, [])

  const press = useCallback((dir, on) => {
    keysRef.current[dir] = on
  }, [])

  const doEstop = useCallback(async () => {
    keysRef.current = { fwd: false, back: false, left: false, right: false }
    movingRef.current = false
    if (!selRef.current) return
    await post(`/api/robot/${encodeURIComponent(selRef.current)}/estop`, {})
    setEstopLatch(true)
  }, [])

  // ---- fleet status polling (1 Hz) ----
  useEffect(() => {
    let stop = false
    const tick = async () => {
      try {
        const res = await fetch('/api/robot')
        const d = await res.json()
        if (stop) return
        const list = d.robots || []
        setRobots(list)
        setSel((cur) => {
          if (cur && list.some((r) => r.id === cur)) return cur
          return list.length ? list[0].id : null
        })
      } catch {
        if (!stop) setRobots((prev) => prev.map((r) => ({ ...r, online: false })))
      }
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => { stop = true; clearInterval(id) }
  }, [])

  // keyboard driving (attached once; reads refs so it survives robot switches)
  useEffect(() => {
    const down = (e) => {
      if (e.repeat) return
      if (e.code === 'Space') { e.preventDefault(); doEstop(); return }
      if (e.code === 'KeyE' && !e.ctrlKey && !e.altKey && !e.metaKey) {
        e.preventDefault(); hardStop(); return
      }
      const dir = KEYMAP[e.code]
      if (dir && !e.ctrlKey && !e.altKey && !e.metaKey) {
        e.preventDefault()
        press(dir, true)
      }
    }
    const up = (e) => {
      const dir = KEYMAP[e.code]
      if (dir) press(dir, false)
    }
    const clearAll = () => {
      keysRef.current = { fwd: false, back: false, left: false, right: false }
      sendStop()
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

  // ---- 10 Hz command heartbeat while buttons/keys are held ----
  // Commands only go to the SELECTED robot; offline robots get nothing
  // (their hub-side staleness would zero the cmd anyway).
  useEffect(() => {
    const id = setInterval(async () => {
      if (!sel) return
      const { vx, wz } = axisValues()
      if (vx !== 0 || wz !== 0) {
        movingRef.current = true
        await post(api('/cmd'), { vx, wz })
      } else if (movingRef.current) {
        await sendStop()
      }
    }, 100)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [axisValues, sendStop, sel])

  const pickRobot = (id) => {
    if (id === sel) return
    keysRef.current = { fwd: false, back: false, left: false, right: false }
    if (movingRef.current) {
      movingRef.current = false
      post(`/api/robot/${encodeURIComponent(sel)}/stop`, {})  // stop the old one
    }
    setSel(id)
    setCamKey((k) => k + 1)
  }

  const battV = status?.battery_v
  const battPct = battV == null ? null :
    Math.max(0, Math.min(100, Math.round(((battV - BATT_EMPTY) / (BATT_FULL - BATT_EMPTY)) * 100)))

  const connected = !!status?.online

  const presets = [
    { name: '🐢 crawl', lin: 0.15, rot: 0.25 },
    { name: 'walk', lin: 0.30, rot: 0.50 },
    { name: '🐇 max', lin: maxLin, rot: maxRot },
  ]
  const applyPreset = (p) => { setLin(p.lin); setRot(p.rot) }

  return (
    <div className="app">
      <header className="topbar">
        <h1>WHEELTEC <span>Fleet WebCtl</span></h1>
        <div className="fleet">
          {robots.length === 1 && <span className="dim one">1 robot ·</span>}
          {robots.map((r) => (
            <button key={r.id}
                    className={`rchip ${r.id === sel ? 'sel' : ''}`}
                    onClick={() => pickRobot(r.id)}
                    title={`${r.id}: ${r.online ? 'online' : 'offline'}`}>
              <span className={`dot ${r.online ? 'ok' : 'bad'}`} />
              {r.id}
              <small>{r.battery_v != null ? `${r.battery_v.toFixed(1)}V` : '--'}</small>
              {r.estop && <span className="mini-estop">STOP</span>}
            </button>
          ))}
          {robots.length === 0 && <span className="dim">no robots registered</span>}
        </div>
        <div className="badges">
          <span className={`dot ${connected ? 'ok' : 'bad'}`} />
          <span>{sel ? (connected ? `${sel} online` : `${sel} OFFLINE`) : '—'}</span>
          {status?.estop && <span className="estopbadge">E-STOP latched</span>}
          <span className="batt">
            {battV != null ? `${battV.toFixed(1)} V` : '-- V'}
            {battPct != null && (
              <span className="bar"><i style={{ width: `${battPct}%` }} /></span>
            )}
          </span>
        </div>
      </header>

      <main className="grid">
        <div className="leftcol">
        <section className="video">
          {!sel ? (
            <div className="camfail">
              <p>No robot selected.</p>
              <p className="dim">Register more robots by adding entries to
                {' '}WEBCTL_ROBOTS on the server — the fleet scales with it.</p>
            </div>
          ) : (
            <WsCam key={`${sel}-${camKey}`} robotId={sel} status={status} />
          )}
        </section>

        <section className="lidar">
          {sel && <ScanView key={sel} robotId={sel} status={status} />}
        </section>
        </div>

        <section className="drive">
          <div className="dpad" onContextMenu={(e) => e.preventDefault()}>
            <DriveBtn label="▲" dir="fwd" press={press} disabled={!connected} />
            <div className="dpad-mid">
              <DriveBtn label="◀" dir="left" press={press} disabled={!connected} />
              <div className="dpad-center">hold to move<br />WASD/arrows ok</div>
              <DriveBtn label="▶" dir="right" press={press} disabled={!connected} />
            </div>
            <DriveBtn label="▼" dir="back" press={press} disabled={!connected} />
          </div>

          <div className="row presets">
            {presets.map((p) => (
              <button key={p.name}
                      className={`preset ${Math.abs(lin - p.lin) < 0.001 ? 'on' : ''}`}
                      onClick={() => applyPreset(p)}>
                {p.name}
                <small>{p.lin.toFixed(2)} m/s</small>
              </button>
            ))}
          </div>

          <div className="sliders">
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

          <div className="row">
            <button className="stopbtn" onClick={hardStop}>⏹ STOP <small>(E)</small></button>
            {estopLatch && (
              <button className="clearbtn" onClick={hardStop}>✓ CLEAR E-STOP</button>
            )}
          </div>

          <button className="estop" onPointerDown={doEstop}>
            ■ E-STOP <small>(space)</small>
          </button>

          <p className="hint">
            Robot stops if commands lapse &gt; {status?.cmd_timeout ?? 0.5}s (server) or its
            own link dies (onboard watchdog). Page blur/hide always sends stop.
          </p>
        </section>
      </main>

      <footer className="foot">
        {status ? (
          <span>
            {status.id} · applied {status.applied.vx.toFixed(2)} m/s {status.applied.wz.toFixed(2)} rad/s ·
            link state {status.applied.vx !== 0 || status.applied.wz !== 0 ? 'driving' : 'idle'} ·
            watchdog trips {status.watchdog_trips} · seq {status.seq} ·
            cam {status.camera ? 'live' : 'off'} ·
            lidar {status.scan ? 'live' : 'off'} ·
            clamps ±{maxLin} m/s ±{maxRot} rad/s
          </span>
        ) : <span>connecting…</span>}
      </footer>
    </div>
  )
}

function WsCam({ robotId, status }) {
  // Primary path: /ws/cam/<id> binary frames (8-byte float64 server ts +
  // JPEG). If no WS server (WSGI-only fallback) we degrade to the legacy
  // /cam/<id> HTTP MJPEG relay, then to an error card.
  const imgRef = useRef(null)
  const [mode, setMode] = useState('ws')     // ws -> mjpeg -> down
  const [fps, setFps] = useState(0)
  const [lat, setLat] = useState(null)

  useEffect(() => {
    if (mode !== 'ws') return undefined
    let ws = null, frames = 0, lastAge = null, closed = false
    try {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/ws/cam/${encodeURIComponent(robotId)}`)
    } catch { setMode('mjpeg'); return undefined }
    ws.binaryType = 'arraybuffer'
    ws.onmessage = (e) => {
      const buf = e.data
      if (!(buf instanceof ArrayBuffer) || buf.byteLength < 12) return
      frames += 1
      const dv = new DataView(buf)
      lastAge = Date.now() - dv.getFloat64(0, true) * 1000
      const blob = new Blob([buf.slice(8)], { type: 'image/jpeg' })
      const next = URL.createObjectURL(blob)
      const img = imgRef.current
      if (img) {
        const prev = img.src
        img.src = next
        if (prev && prev.startsWith('blob:')) URL.revokeObjectURL(prev)
      } else {
        URL.revokeObjectURL(next)
      }
    }
    ws.onerror = () => { /* onclose decides fallback */ }
    ws.onclose = () => { closed = true; setMode((m) => (m === 'ws' ? 'mjpeg' : m)) }
    const iv = setInterval(() => { setFps(frames); frames = 0; setLat(lastAge) }, 1000)
    return () => { clearInterval(iv); if (!closed) { try { ws && ws.close() } catch {} } }
  }, [robotId, mode])

  if (mode === 'down') {
    return (
      <div className="camfail">
        <p>Camera stream unavailable for {robotId}.</p>
        <p className="dim">Robot client camera state: {String(status?.camera)} ·{' '}
          robot-side ok: {String(status?.robot_cam_ok)}</p>
        {status && <p className="dim hintsmall">{sensorHint(status, 'image')}</p>}
        <button onClick={() => setMode('ws')}>Retry stream</button>
      </div>
    )
  }
  if (mode === 'mjpeg') {
    return (
      <div className="wsview">
        <img src={`/cam/${encodeURIComponent(robotId)}`} alt={`${robotId} camera`}
             onError={() => setMode('down')} />
        <span className="camtag">http mjpeg fallback</span>
      </div>
    )
  }
  return (
    <div className="wsview">
      <img ref={imgRef} alt={`${robotId} camera`} />
      <span className="camtag">
        ◉ ws {fps} fps{lat != null ? ` · ${Math.max(0, Math.round(lat))} ms` : ' · …'}
      </span>
    </div>
  )
}

function ScanView({ robotId, status }) {
  // LiDAR panel: polls /api/robot/<id>/scan (~3 Hz) and draws the newest
  // packed LaserScan. ROS convention: +x forward (up on screen), angles
  // counterclockwise, 0.0 range = no return (left as a gap).
  const canvasRef = useRef(null)
  const scanRef = useRef(null)
  const [meta, setMeta] = useState({ points: 0, age: null, fresh: false, err: false })

  useEffect(() => {
    let stop = false
    const tick = async () => {
      try {
        const res = await fetch(`/api/robot/${encodeURIComponent(robotId)}/scan`)
        const d = await res.json()
        if (stop) return
        if (d.scan) scanRef.current = d.scan
        setMeta({ points: d.scan ? d.scan.ranges.length : 0,
                  age: d.age, fresh: !!d.fresh, err: false })
      } catch {
        if (!stop) setMeta((m) => ({ ...m, err: true }))
      }
    }
    tick()
    const id = setInterval(tick, 300)
    return () => { stop = true; clearInterval(id) }
  }, [robotId])

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
    }
    draw()
    window.addEventListener('resize', draw)
    return () => window.removeEventListener('resize', draw)
  }, [meta])

  const ageTxt = meta.age == null ? '—' : `${Math.round(meta.age * 1000)} ms`
  return (
    <>
      <canvas ref={canvasRef} />
      <span className={`camtag lidar-tag ${meta.fresh ? '' : 'stale'}`}>
        {meta.err ? 'lidar: server unreachable'
          : meta.fresh ? `◉ lidar ${meta.points} rays · ${ageTxt}`
          : `lidar idle · last ${ageTxt}`}
      </span>
      {!meta.fresh && status?.online && (
        <div className="lidhint">{sensorHint(status, 'scan')}</div>
      )}
    </>
  )
}

function DriveBtn({ label, dir, press, disabled }) {
  const [active, setActive] = useState(false)
  const on = (e) => {
    e.preventDefault()
    if (disabled) return
    setActive(true); press(dir, true)
  }
  const off = () => { setActive(false); press(dir, false) }
  return (
    <button
      className={`dbtn ${active ? 'active' : ''} ${disabled ? 'off' : ''}`}
      disabled={disabled}
      onPointerDown={on} onPointerUp={off} onPointerLeave={off}
      onPointerCancel={off}>
      {label}
    </button>
  )
}
