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
  const [camError, setCamError] = useState(false)

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
    setCamError(false)
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
        <section className="video">
          {!sel || camError ? (
            <div className="camfail">
              <p>Camera stream unavailable{sel ? ` for ${sel}` : ''}.</p>
              <p className="dim">That robot's client must run with WEBCTL_CAM_URL
                {' '}set to its local MJPEG source.</p>
              {sel && <button onClick={() => { setCamError(false); setCamKey(camKey + 1) }}>Retry stream</button>}
            </div>
          ) : (
            <img key={`${sel}-${camKey}`} src={`/cam/${encodeURIComponent(sel)}`}
                 alt={`${sel} camera`} onError={() => setCamError(true)} />
          )}
        </section>

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
            clamps ±{maxLin} m/s ±{maxRot} rad/s
          </span>
        ) : <span>connecting…</span>}
      </footer>
    </div>
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
