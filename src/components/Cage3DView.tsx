import { useCallback, useEffect, useRef, useState } from 'react'
import type { CagePlan, Placement, Vec3 } from '../types'

interface Face {
  points: Vec3[]
  normal: Vec3
  color: string
  depth: number
}

const LIGHT_DIR: Vec3 = { x: 0.35, y: -0.55, z: 0.76 }
const BIN = { w: 1150, d: 750, h: 1650 }

function normalize(v: Vec3): Vec3 {
  const m = Math.hypot(v.x, v.y, v.z) || 1
  return { x: v.x / m, y: v.y / m, z: v.z / m }
}

function dot(a: Vec3, b: Vec3): number {
  return a.x * b.x + a.y * b.y + a.z * b.z
}

function hueFromString(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360
  return h
}

function cubeFaces(p: Placement): Array<{ points: Vec3[]; normal: Vec3 }> {
  const { x, y, z, w, d, h } = p
  const v: Vec3[] = [
    { x, y, z },
    { x: x + w, y, z },
    { x: x + w, y: y + d, z },
    { x, y: y + d, z },
    { x, y, z: z + h },
    { x: x + w, y, z: z + h },
    { x: x + w, y: y + d, z: z + h },
    { x, y: y + d, z: z + h },
  ]
  const f = (i: number[]): Vec3[] => i.map((k) => v[k])
  return [
    { points: f([4, 5, 6, 7]), normal: { x: 0, y: 0, z: 1 } },
    { points: f([0, 3, 2, 1]), normal: { x: 0, y: 0, z: -1 } },
    { points: f([0, 1, 5, 4]), normal: { x: 0, y: -1, z: 0 } },
    { points: f([1, 2, 6, 5]), normal: { x: 1, y: 0, z: 0 } },
    { points: f([2, 3, 7, 6]), normal: { x: 0, y: 1, z: 0 } },
    { points: f([3, 0, 4, 7]), normal: { x: -1, y: 0, z: 0 } },
  ]
}

export function Cage3DView({
  cage,
  height = 360,
  autoRotate = true,
}: {
  cage: CagePlan
  height?: number
  autoRotate?: boolean
}) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const yawRef = useRef(38)
  const spinRef = useRef(autoRotate)
  const showRef = useRef<'all' | number>('all')
  const sizeRef = useRef({ w: 0, h: 0, dpr: 0 })

  const [spin, setSpin] = useState(autoRotate)
  const [yawUi, setYawUi] = useState(38)
  const [show, setShow] = useState<'all' | number>('all')

  const render = useCallback(() => {
    const canvas = canvasRef.current
    const wrap = wrapRef.current
    if (!canvas || !wrap) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const dpr = window.devicePixelRatio || 1
    const W = wrap.clientWidth
    const H = height
    const size = sizeRef.current
    if (size.w !== W || size.h !== H || size.dpr !== dpr) {
      canvas.width = Math.max(1, Math.round(W * dpr))
      canvas.height = Math.max(1, Math.round(H * dpr))
      canvas.style.width = `${W}px`
      canvas.style.height = `${H}px`
      sizeRef.current = { w: W, h: H, dpr }
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

    const az = (yawRef.current * Math.PI) / 180
    const el = (28 * Math.PI) / 180
    const d = normalize({ x: Math.cos(el) * Math.cos(az), y: Math.cos(el) * Math.sin(az), z: Math.sin(el) })
    const right = normalize({ x: -Math.sin(az), y: Math.cos(az), z: 0 })
    const up = normalize({ x: -Math.sin(el) * Math.cos(az), y: -Math.sin(el) * Math.sin(az), z: Math.cos(el) })

    const project = (p: Vec3) => ({ X: dot(p, right), Y: dot(p, up), depth: dot(p, d) })

    const corners: Vec3[] = [
      { x: 0, y: 0, z: 0 },
      { x: BIN.w, y: 0, z: 0 },
      { x: BIN.w, y: BIN.d, z: 0 },
      { x: 0, y: BIN.d, z: 0 },
      { x: 0, y: 0, z: BIN.h },
      { x: BIN.w, y: 0, z: BIN.h },
      { x: BIN.w, y: BIN.d, z: BIN.h },
      { x: 0, y: BIN.d, z: BIN.h },
    ]
    const proj = corners.map(project)
    const minX = Math.min(...proj.map((p) => p.X))
    const maxX = Math.max(...proj.map((p) => p.X))
    const minY = Math.min(...proj.map((p) => p.Y))
    const maxY = Math.max(...proj.map((p) => p.Y))
    const margin = 34
    const scale = Math.min((W - margin * 2) / (maxX - minX || 1), (H - margin * 2) / (maxY - minY || 1))
    const toScreen = (p: Vec3) => {
      const pr = project(p)
      return { x: margin + (pr.X - minX) * scale, y: margin + (maxY - pr.Y) * scale }
    }

    ctx.clearRect(0, 0, W, H)

    ctx.strokeStyle = 'rgba(90, 120, 150, 0.45)'
    ctx.lineWidth = 1
    const edges: Array<[number, number]> = [
      [0, 1], [1, 2], [2, 3], [3, 0],
      [4, 5], [5, 6], [6, 7], [7, 4],
      [0, 4], [1, 5], [2, 6], [3, 7],
    ]
    for (const [a, b] of edges) {
      const pa = toScreen(corners[a])
      const pb = toScreen(corners[b])
      ctx.beginPath()
      ctx.moveTo(pa.x, pa.y)
      ctx.lineTo(pb.x, pb.y)
      ctx.stroke()
    }

    ctx.strokeStyle = 'rgba(60, 90, 120, 0.22)'
    for (let gx = 0; gx <= BIN.w; gx += 200) {
      const a = toScreen({ x: gx, y: 0, z: 0 })
      const b = toScreen({ x: gx, y: BIN.d, z: 0 })
      ctx.beginPath()
      ctx.moveTo(a.x, a.y)
      ctx.lineTo(b.x, b.y)
      ctx.stroke()
    }
    for (let gy = 0; gy <= BIN.d; gy += 200) {
      const a = toScreen({ x: 0, y: gy, z: 0 })
      const b = toScreen({ x: BIN.w, y: gy, z: 0 })
      ctx.beginPath()
      ctx.moveTo(a.x, a.y)
      ctx.lineTo(b.x, b.y)
      ctx.stroke()
    }

    const sel = showRef.current
    const placements = sel === 'all' ? cage.placements : cage.placements.filter((_, i) => i === sel)

    const faces: Face[] = []
    for (const p of placements) {
      const hue = hueFromString(p.skuId)
      const highlight = sel !== 'all'
      for (const face of cubeFaces(p)) {
        if (dot(face.normal, d) <= 0.01) continue
        const lum = 0.4 + 0.32 * Math.max(0, face.normal.z) + 0.18 * Math.max(0, dot(face.normal, LIGHT_DIR))
        const color = `hsl(${hue} 70% ${Math.round((highlight ? 58 : 46) * lum + 18)}%)`
        const depth = face.points.reduce((acc, pt) => acc + dot(pt, d), 0) / face.points.length
        faces.push({ points: face.points, normal: face.normal, color, depth })
      }
    }

    faces.sort((a, b) => a.depth - b.depth)
    for (const face of faces) {
      ctx.beginPath()
      face.points.forEach((pt, i) => {
        const s = toScreen(pt)
        if (i === 0) ctx.moveTo(s.x, s.y)
        else ctx.lineTo(s.x, s.y)
      })
      ctx.closePath()
      ctx.fillStyle = face.color
      ctx.fill()
      ctx.strokeStyle = 'rgba(0,0,0,0.35)'
      ctx.lineWidth = 0.6
      ctx.stroke()
    }

    if (placements.length === 0) {
      ctx.fillStyle = 'rgba(143,162,184,0.8)'
      ctx.font = '13px sans-serif'
      ctx.textAlign = 'center'
      ctx.fillText('Empty cage', W / 2, H / 2)
    }
  }, [cage, height])

  // Imperative rAF loop: no React re-render per frame (fixes stutter).
  useEffect(() => {
    let raf = 0
    let last = performance.now()
    const loop = (t: number) => {
      const dt = Math.min(0.05, (t - last) / 1000)
      last = t
      if (spinRef.current) {
        yawRef.current = (yawRef.current + dt * 24) % 360
      }
      render()
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)

    const ro = new ResizeObserver(() => render())
    if (wrapRef.current) ro.observe(wrapRef.current)
    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
    }
  }, [render])

  return (
    <div>
      <div
        ref={wrapRef}
        style={{
          borderRadius: 10,
          border: '1px solid var(--border)',
          background: 'radial-gradient(400px 200px at 50% 0%, #101923, #0a0f15)',
          overflow: 'hidden',
        }}
      >
        <canvas ref={canvasRef} />
      </div>
      <div className="row between" style={{ marginTop: 10 }}>
        <div className="row" style={{ gap: 14 }}>
          <label className="faint" style={{ fontSize: 12 }}>
            Rotate
          </label>
          <input
            type="range"
            min={0}
            max={360}
            value={Math.round(yawUi)}
            onChange={(e) => {
              spinRef.current = false
              setSpin(false)
              yawRef.current = Number(e.target.value)
              setYawUi(Number(e.target.value))
            }}
            style={{ width: 130, padding: 0 }}
          />
          <button
            className="btn sm ghost"
            onClick={() => {
              const next = !spinRef.current
              spinRef.current = next
              setSpin(next)
            }}
          >
            {spin ? '⏸ Pause' : '▶ Spin'}
          </button>
        </div>
        <select
          value={show === 'all' ? 'all' : String(show)}
          onChange={(e) => {
            const v = e.target.value === 'all' ? 'all' : Number(e.target.value)
            showRef.current = v
            setShow(v)
          }}
          style={{ width: 150 }}
        >
          <option value="all">All items</option>
          {cage.placements.map((p, i) => (
            <option key={i} value={i}>
              #{i + 1} · {p.name}
            </option>
          ))}
        </select>
      </div>
      <div className="legend" style={{ marginTop: 10 }}>
        {[...new Set(cage.placements.map((p) => p.skuId))].map((sku) => (
          <span key={sku}>
            <i style={{ background: `hsl(${hueFromString(sku)} 70% 52%)` }} />
            {sku}
          </span>
        ))}
      </div>
    </div>
  )
}
