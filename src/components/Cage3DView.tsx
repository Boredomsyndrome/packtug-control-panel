import { useCallback, useEffect, useRef, useState } from 'react'
import type { CagePlan, Placement, Size3, Vec3 } from '../types'
import { hueForSku } from '../lib/format'

interface Face {
  points: Vec3[]
  color: string
  depth: number
  active: boolean
  opening?: boolean
}

const LIGHT_DIR: Vec3 = { x: 0.35, y: -0.55, z: 0.76 }
const DEFAULT_BIN: Size3 = { w: 550, d: 250, h: 400 }

const DROP_MS = 720
const APPROACH_Y = 260
const APPROACH_Z = 26

// Industrial arm (Ned2-like) placed outside the opening, reaching in sideways.
const ARM_STANDOFF = 200
const SHOULDER_Z = 180
const L1 = 250
const L2 = 240

const ACCENT = '#22d3ee'
const ARM = '#f59e0b'
const ARM_DARK = '#b45309'

function vsub(a: Vec3, b: Vec3): Vec3 {
  return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z }
}
function vadd(a: Vec3, b: Vec3): Vec3 {
  return { x: a.x + b.x, y: a.y + b.y, z: a.z + b.z }
}
function vscale(a: Vec3, s: number): Vec3 {
  return { x: a.x * s, y: a.y * s, z: a.z * s }
}
function vlen(a: Vec3): number {
  return Math.hypot(a.x, a.y, a.z)
}
function normalize(v: Vec3): Vec3 {
  const m = vlen(v) || 1
  return { x: v.x / m, y: v.y / m, z: v.z / m }
}
function dot(a: Vec3, b: Vec3): number {
  return a.x * b.x + a.y * b.y + a.z * b.z
}
function easeOut(t: number): number {
  return 1 - Math.pow(1 - t, 3)
}
function roundRectPath(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.arcTo(x + w, y, x + w, y + h, r)
  ctx.arcTo(x + w, y + h, x, y + h, r)
  ctx.arcTo(x, y + h, x, y, r)
  ctx.arcTo(x, y, x + w, y, r)
  ctx.closePath()
}

function cubeFaces(p: Placement, zoff = 0, yoff = 0): Array<{ points: Vec3[]; normal: Vec3 }> {
  const { x, y, z, w, d, h } = p
  const zz = z + zoff
  const yy = y + yoff
  const v: Vec3[] = [
    { x, y: yy, z: zz },
    { x: x + w, y: yy, z: zz },
    { x: x + w, y: yy + d, z: zz },
    { x, y: yy + d, z: zz },
    { x, y: yy, z: zz + h },
    { x: x + w, y: yy, z: zz + h },
    { x: x + w, y: yy + d, z: zz + h },
    { x, y: yy + d, z: zz + h },
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
  bin = DEFAULT_BIN,
  height = 380,
  autoRotate = true,
  visibleCount,
  dropKey,
  showLabels = true,
}: {
  cage: CagePlan
  bin?: Size3
  height?: number
  autoRotate?: boolean
  visibleCount?: number
  dropKey?: number
  showLabels?: boolean
}) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const yawRef = useRef(38)
  const spinRef = useRef(autoRotate)
  const labelsRef = useRef(showLabels)
  const sizeRef = useRef({ w: 0, h: 0, dpr: 0 })
  const dropStartRef = useRef(0)

  const [spin, setSpin] = useState(autoRotate)
  const [labels, setLabels] = useState(showLabels)

  const animating = dropKey !== undefined

  useEffect(() => {
    labelsRef.current = labels
  }, [labels])

  useEffect(() => {
    dropStartRef.current = performance.now()
  }, [dropKey])

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
      { x: bin.w, y: 0, z: 0 },
      { x: bin.w, y: bin.d, z: 0 },
      { x: 0, y: bin.d, z: 0 },
      { x: 0, y: 0, z: bin.h },
      { x: bin.w, y: 0, z: bin.h },
      { x: bin.w, y: bin.d, z: bin.h },
      { x: 0, y: bin.d, z: bin.h },
    ]
    // fit the view to include the arm + side-approach region in front of the cage
    const fitPts: Vec3[] = [
      ...corners,
      { x: 0, y: -360, z: 0 },
      { x: bin.w, y: -360, z: 0 },
      { x: 0, y: -360, z: bin.h },
      { x: bin.w, y: -360, z: bin.h },
    ]
    const proj = fitPts.map(project)
    const minX = Math.min(...proj.map((p) => p.X))
    const maxX = Math.max(...proj.map((p) => p.X))
    const minY = Math.min(...proj.map((p) => p.Y))
    const maxY = Math.max(...proj.map((p) => p.Y))
    const margin = 40
    const scale = Math.min((W - margin * 2) / (maxX - minX || 1), (H - margin * 2) / (maxY - minY || 1))
    const toScreen = (p: Vec3) => {
      const pr = project(p)
      return { x: margin + (pr.X - minX) * scale, y: margin + (maxY - pr.Y) * scale }
    }
    const drawSeg = (a: Vec3, b: Vec3, color: string, width: number) => {
      const pa = toScreen(a)
      const pb = toScreen(b)
      ctx.strokeStyle = color
      ctx.lineWidth = width
      ctx.lineCap = 'round'
      ctx.beginPath()
      ctx.moveTo(pa.x, pa.y)
      ctx.lineTo(pb.x, pb.y)
      ctx.stroke()
    }

    ctx.clearRect(0, 0, W, H)

    // cage wireframe
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

    // floor grid
    ctx.strokeStyle = 'rgba(60, 90, 120, 0.22)'
    for (let gx = 0; gx <= bin.w; gx += 100) {
      const a = toScreen({ x: gx, y: 0, z: 0 })
      const b = toScreen({ x: gx, y: bin.d, z: 0 })
      ctx.beginPath()
      ctx.moveTo(a.x, a.y)
      ctx.lineTo(b.x, b.y)
      ctx.stroke()
    }
    for (let gy = 0; gy <= bin.d; gy += 100) {
      const a = toScreen({ x: 0, y: gy, z: 0 })
      const b = toScreen({ x: bin.w, y: gy, z: 0 })
      ctx.beginPath()
      ctx.moveTo(a.x, a.y)
      ctx.lineTo(b.x, b.y)
      ctx.stroke()
    }

    // gather faces
    const faces: Face[] = []

    // cage opening (arm inlet) — framed panel at y = 0
    const openingPts: Vec3[] = [
      { x: 0, y: 0, z: 0 },
      { x: bin.w, y: 0, z: 0 },
      { x: bin.w, y: 0, z: bin.h },
      { x: 0, y: 0, z: bin.h },
    ]
    faces.push({
      points: openingPts,
      color: '',
      depth: openingPts.reduce((acc, pt) => acc + dot(pt, d), 0) / openingPts.length,
      active: false,
      opening: true,
    })

    const total = cage.placements.length
    const n = visibleCount === undefined ? total : Math.max(0, Math.min(total, visibleCount))
    const shown = cage.placements.slice(0, n)
    const activeSeq = animating && n > 0 ? shown[n - 1].seq : -1
    const now = performance.now()
    const tRaw = animating ? Math.min(1, (now - dropStartRef.current) / DROP_MS) : 1
    const eased = easeOut(tRaw)
    const dropping = animating && tRaw < 1

    // side-placement approach: slide in from the opening (negative y), small lift
    const activeZoff = dropping ? (1 - eased) * APPROACH_Z : 0
    const activeYoff = dropping ? -(1 - eased) * APPROACH_Y : 0

    for (const p of shown) {
      const active = p.seq === activeSeq
      const zoff = active ? activeZoff : 0
      const yoff = active ? activeYoff : 0
      const hue = hueForSku(p.skuId)
      for (const face of cubeFaces(p, zoff, yoff)) {
        if (dot(face.normal, d) <= 0.01) continue
        const lum = 0.4 + 0.32 * Math.max(0, face.normal.z) + 0.18 * Math.max(0, dot(face.normal, LIGHT_DIR))
        const light = active ? 60 : 46
        const color = `hsl(${hue} 72% ${Math.round(light * lum + 18)}%)`
        const depth = face.points.reduce((acc, pt) => acc + dot(pt, d), 0) / face.points.length
        faces.push({ points: face.points, color, depth, active })
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
      if (face.opening) {
        ctx.setLineDash([7, 5])
        ctx.strokeStyle = 'rgba(34,211,238,0.95)'
        ctx.lineWidth = 2.2
        ctx.stroke()
        ctx.setLineDash([])
        continue
      }
      ctx.fillStyle = face.color
      ctx.fill()
      if (face.active) {
        ctx.strokeStyle = ACCENT
        ctx.lineWidth = 1.6
      } else {
        ctx.strokeStyle = 'rgba(0,0,0,0.35)'
        ctx.lineWidth = 0.6
      }
      ctx.stroke()
    }

    // target footprint while the carton is sliding in
    if (dropping && n > 0) {
      const p = shown[n - 1]
      const foot: Vec3[] = [
        { x: p.x, y: p.y, z: 0 },
        { x: p.x + p.w, y: p.y, z: 0 },
        { x: p.x + p.w, y: p.y + p.d, z: 0 },
        { x: p.x, y: p.y + p.d, z: 0 },
      ]
      ctx.setLineDash([5, 4])
      ctx.strokeStyle = 'rgba(34,211,238,0.7)'
      ctx.lineWidth = 1.4
      ctx.beginPath()
      foot.forEach((pt, i) => {
        const s = toScreen(pt)
        if (i === 0) ctx.moveTo(s.x, s.y)
        else ctx.lineTo(s.x, s.y)
      })
      ctx.closePath()
      ctx.stroke()
      ctx.setLineDash([])
    }

    // ---- simplified robotic arm (2-bone IK) ----
    if (animating) {
      const base: Vec3 = { x: bin.w / 2, y: -ARM_STANDOFF, z: 0 }
      const shoulder: Vec3 = { x: bin.w / 2, y: -ARM_STANDOFF, z: SHOULDER_Z }
      let target: Vec3
      if (n > 0) {
        const p = shown[n - 1]
        target = {
          x: p.x + p.w / 2,
          y: p.y + p.d / 2 + (p.seq === activeSeq ? activeYoff : 0),
          z: p.z + p.h / 2 + (p.seq === activeSeq ? activeZoff : 0),
        }
      } else {
        target = { x: bin.w / 2, y: -ARM_STANDOFF - 150, z: SHOULDER_Z + 90 }
      }

      const v = vsub(target, shoulder)
      let dist = vlen(v)
      const reachMax = L1 + L2 - 2
      if (dist > reachMax) {
        target = vadd(shoulder, vscale(normalize(v), reachMax))
        dist = reachMax
      }
      dist = Math.max(dist, Math.abs(L1 - L2) + 2)
      const u = normalize(vsub(target, shoulder))
      let nrm = vsub({ x: 0, y: 0, z: 1 }, vscale(u, dot({ x: 0, y: 0, z: 1 }, u)))
      if (vlen(nrm) < 1e-3) {
        nrm = vsub({ x: 0, y: 1, z: 0 }, vscale(u, dot({ x: 0, y: 1, z: 0 }, u)))
      }
      nrm = normalize(nrm)
      const a = (L1 * L1 - L2 * L2 + dist * dist) / (2 * dist)
      const hgt = Math.sqrt(Math.max(0, L1 * L1 - a * a))
      const elbow = vadd(vadd(shoulder, vscale(u, a)), vscale(nrm, hgt))

      // base column
      drawSeg(base, shoulder, '#334155', 9)
      drawSeg(shoulder, elbow, ARM, 8)
      drawSeg(elbow, target, ARM, 7)
      drawSeg(elbow, target, ARM_DARK, 2)
      // gripper jaws
      const jawL: Vec3 = { x: target.x - 20, y: target.y + 16, z: target.z }
      const jawR: Vec3 = { x: target.x + 20, y: target.y + 16, z: target.z }
      drawSeg(target, jawL, '#1f2937', 4)
      drawSeg(target, jawR, '#1f2937', 4)

      // joints
      for (const [pt, r] of [
        [shoulder, 5],
        [elbow, 5],
        [target, 3.5],
      ] as Array<[Vec3, number]>) {
        const s = toScreen(pt)
        ctx.beginPath()
        ctx.arc(s.x, s.y, r, 0, Math.PI * 2)
        ctx.fillStyle = '#0b121a'
        ctx.fill()
        ctx.strokeStyle = ARM
        ctx.lineWidth = 2
        ctx.stroke()
      }
    }

    // sequence numbers
    if (labelsRef.current) {
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.font = 'bold 11px ui-monospace, monospace'
      for (const p of shown) {
        const active = p.seq === activeSeq
        const zoff = active ? activeZoff : 0
        const yoff = active ? activeYoff : 0
        const s = toScreen({ x: p.x + p.w / 2, y: p.y + p.d / 2 + yoff, z: p.z + p.h + zoff + 1 })
        ctx.fillStyle = 'rgba(0,0,0,0.55)'
        ctx.beginPath()
        ctx.arc(s.x, s.y, 8, 0, Math.PI * 2)
        ctx.fill()
        ctx.fillStyle = active ? ACCENT : 'rgba(255,255,255,0.92)'
        ctx.fillText(String(p.seq), s.x, s.y + 0.5)
      }
    }

    // opening marker label
    {
      const anchor = toScreen({ x: bin.w / 2, y: 0, z: bin.h })
      const label = '开口 · OPENING (side inlet)'
      ctx.font = 'bold 11.5px ui-sans-serif, system-ui, sans-serif'
      const tw = ctx.measureText(label).width
      const bw = tw + 18
      const bh = 22
      const bx = Math.min(W - bw - 8, Math.max(8, anchor.x - bw / 2))
      const by = Math.max(6, anchor.y - bh - 14)

      ctx.strokeStyle = 'rgba(34,211,238,0.75)'
      ctx.lineWidth = 1.2
      ctx.beginPath()
      ctx.moveTo(anchor.x, by + bh)
      ctx.lineTo(anchor.x, anchor.y)
      ctx.stroke()
      ctx.fillStyle = ACCENT
      ctx.beginPath()
      ctx.moveTo(anchor.x - 4.5, anchor.y - 6)
      ctx.lineTo(anchor.x, anchor.y)
      ctx.lineTo(anchor.x + 4.5, anchor.y - 6)
      ctx.closePath()
      ctx.fill()

      roundRectPath(ctx, bx, by, bw, bh, 6)
      ctx.fillStyle = 'rgba(6,34,42,0.95)'
      ctx.fill()
      ctx.strokeStyle = 'rgba(34,211,238,0.95)'
      ctx.lineWidth = 1
      ctx.stroke()
      ctx.fillStyle = ACCENT
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(label, bx + bw / 2, by + bh / 2 + 0.5)
    }

    if (shown.length === 0) {
      ctx.fillStyle = 'rgba(143,162,184,0.8)'
      ctx.font = '13px sans-serif'
      ctx.textAlign = 'center'
      ctx.fillText(animating ? 'Press ▶ to place cartons' : 'Empty cage', W / 2, H / 2)
    }
  }, [cage, bin, height, visibleCount, dropKey, animating])

  useEffect(() => {
    let raf = 0
    let last = performance.now()
    const loop = (t: number) => {
      const dt = Math.min(0.05, (t - last) / 1000)
      last = t
      if (spinRef.current) yawRef.current = (yawRef.current + dt * 24) % 360
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
        <div className="row" style={{ gap: 12 }}>
          <label className="faint" style={{ fontSize: 12 }}>
            Rotate
          </label>
          <input
            type="range"
            min={0}
            max={360}
            defaultValue={38}
            onChange={(e) => {
              spinRef.current = false
              setSpin(false)
              yawRef.current = Number(e.target.value)
            }}
            style={{ width: 110, padding: 0 }}
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
          <button
            className="btn sm ghost"
            title="Stop rotation"
            onClick={() => {
              spinRef.current = false
              setSpin(false)
              yawRef.current = 38
            }}
          >
            ⏹ Stop
          </button>
          <label className="row" style={{ fontSize: 12, gap: 6, cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={labels}
              onChange={(e) => setLabels(e.target.checked)}
              style={{ width: 'auto' }}
            />
            <span className="faint">Numbers</span>
          </label>
        </div>
        <div className="legend" style={{ marginTop: 0 }}>
          {[...new Set(cage.placements.map((p) => p.skuId))].slice(0, 4).map((sku) => (
            <span key={sku}>
              <i style={{ background: `hsl(${hueForSku(sku)} 70% 52%)` }} />
              {sku}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}
