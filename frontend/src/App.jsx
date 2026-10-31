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
  const [status, setStatus] = useState(null)
  const [lin, setLin] = useState(0.3)     // m/s applied while forward/back held
  const [rot, setRot] = useState(0.5)     // rad/s applied while turning held
  const [estopLatch, setEstopLatch] = useState(false)
  const [camKey, setCamKey] = useState(0)
  const [camError, setCamError] = useState(false)

  const keysRef = useRef({ fwd: false, back: false, left: false, right: false })
  const movingRef = useRef(false)
  const maxLin = status?.max_lin ?? 0.5
  const maxRot = status?.max_rot ?? 0.7

  const axisValues = useCallback(() => {
    const k = keysRef.current
    const vx = (k.fwd ? lin : 0) + (k.back ? -lin : 0)
    const wz = (k.left ? rot : 0) + (k.right ? -rot : 0)
    return { vx, wz }
  }, [lin, rot])

  const sendStop = useCallback(async () => {
    if (movingRef.current) {
      movingRef.current = false
      await post('/api/robot/stop', {})
    }
  }, [])

  // Hard stop: clears held buttons/keys AND latched e-stop server-side.
  const hardStop = useCallback(async () => {
    keysRef.current = { fwd: false, back: false, left: false, right: false }
    movingRef.current = false
    setEstopLatch(false)
    await post('/api/robot/stop', {})
  }, [])

  const press = useCallback((dir, on) => {
    keysRef.current[dir] = on
  }, [])

  const doEstop = useCallback(async () => {
    keysRef.current = { fwd: false, back: false, left: false, right: false }
    movingRef.current = false
    await post('/api/robot/estop', {})
    setEstopLatch(true)
  }, [])

  // ---- status polling (1 Hz) ----
  useEffect(() => {
    let stop = false
    const tick = async () => {
      try {
        const res = await fetch('/api/robot')
        const s = await res.json()
        if (!stop) {
          setStatus(s)
          setEstopLatch(!!s.estop)
        }
      } catch {
        if (!stop) setStatus((prev) => ({ ...(prev || {}), connected: false }))
      }
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => { stop = true; clearInterval(id) }
  }, [])

  // ---- keyboard driving ----
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
  useEffect(() => {
    const id = setInterval(async () => {
      const { vx, wz } = axisValues()
      if (vx !== 0 || wz !== 0) {
        movingRef.current = true
        await post('/api/robot/cmd', { vx, wz })
      } else if (movingRef.current) {
        await sendStop()
      }
    }, 100)
    return () => clearInterval(id)
  }, [axisValues, sendStop])

  const battV = status?.battery_v
  const battPct = battV == null ? null :
    Math.max(0, Math.min(100, Math.round(((battV - BATT_EMPTY) / (BATT_FULL - BATT_EMPTY)) * 100)))

  const connected = !!status?.connected

  const presets = [
    { name: '🐢 crawl', lin: 0.15, rot: 0.25 },
    { name: 'walk', lin: 0.30, rot: 0.50 },
    { name: '🐇 max', lin: maxLin, rot: maxRot },
  ]
  const applyPreset = (p) => { setLin(p.lin); setRot(p.rot) }

  return (
    <div className="app">
      <header className="topbar">
        <h1>WHEELTEC <span>WebCtl</span></h1>
        <div className="badges">
          <span className={`dot ${connected ? 'ok' : 'bad'}`} />
          <span>{connected ? 'robot connected' : 'robot OFFLINE'}</span>
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
          {camError ? (
            <div className="camfail">
              <p>Camera stream unavailable.</p>
              <p className="dim">Check the robot’s <code>web_video_server</code> and CAMERA_TOPIC.</p>
              <button onClick={() => { setCamError(false); setCamKey(camKey + 1) }}>Retry stream</button>
            </div>
          ) : (
            <img key={camKey} src="/cam" alt="robot camera"
                 onError={() => setCamError(true)} />
          )}
        </section>

        <section className="drive">
          <div className="dpad" onContextMenu={(e) => e.preventDefault()}>
            <DriveBtn label="▲" dir="fwd" press={press} />
            <div className="dpad-mid">
              <DriveBtn label="◀" dir="left" press={press} />
              <div className="dpad-center">hold to move<br/>WASD/arrows ok</div>
              <DriveBtn label="▶" dir="right" press={press} />
            </div>
            <DriveBtn label="▼" dir="back" press={press} />
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
            Server watchdog stops the robot if commands lapse &gt;
            {status?.cmd_timeout ?? 0.5}s. Page blur/hide always sends stop.
          </p>
        </section>
      </main>

      <footer className="foot">
        {status ? (
          <span>
            {status.ws_url} · publishes {status.publishes} ·
            cam topic via /cam · clamps ±{maxLin} m/s ±{maxRot} rad/s
          </span>
        ) : <span>connecting…</span>}
      </footer>
    </div>
  )
}

function DriveBtn({ label, dir, press }) {
  const [active, setActive] = useState(false)
  const on = (e) => { e.preventDefault(); setActive(true); press(dir, true) }
  const off = () => { setActive(false); press(dir, false) }
  return (
    <button
      className={`dbtn ${active ? 'active' : ''}`}
      onPointerDown={on} onPointerUp={off} onPointerLeave={off}
      onPointerCancel={off}>
      {label}
    </button>
  )
}
