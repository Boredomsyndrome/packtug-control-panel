// v3.7 (merge): live LiDAR sweep for the panel Fleet page — port of the
// AGV console's polar canvas, reading the hub directly (same origin now).
// /api/robot/<id>/scan {age, fresh, scan:{ranges,amin,ainc,rmin,rmax}}.
// 0.0 range = no return (gap). Single-flight poll with AbortSignal per the
// v3.6.4 connection-starvation lesson.
import { useEffect, useRef, useState } from 'react'
import { vehicleApi, type ScanFrame } from '../services/vehicle'

interface Props {
  robotId: string
  height?: number
}

export function LidarSweep({ robotId, height = 340 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const scanRef = useRef<ScanFrame | null>(null)
  const [meta, setMeta] = useState<{ fresh: boolean; age: number | null; points: number; err: boolean }>({
    fresh: false, age: null, points: 0, err: false,
  })

  useEffect(() => {
    let alive = true
    let busy = false
    async function poll() {
      if (busy || !robotId) return
      busy = true
      try {
        const d = await vehicleApi.getScan(robotId)
        if (!alive) return
        if (d.scan) scanRef.current = d.scan
        setMeta({
          fresh: !!d.fresh && !!d.scan,
          age: d.age ?? null,
          points: d.scan ? d.scan.ranges.length : 0,
          err: false,
        })
      } catch {
        if (alive) setMeta((m) => ({ ...m, err: true }))
      } finally {
        busy = false
      }
    }
    void poll()
    const t = window.setInterval(poll, 900)
    return () => {
      alive = false
      window.clearInterval(t)
    }
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
          const x = cx - r * Math.sin(a) * scale // +x up, +y left
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
    <div style={{ position: 'relative' }}>
      <canvas ref={canvasRef} style={{ width: '100%', height, borderRadius: 10, background: '#0b121a' }} />
      <span className="faint mono" style={{ position: 'absolute', left: 10, bottom: 8, fontSize: 11.5 }}>
        {meta.err ? 'lidar: hub unreachable'
          : meta.fresh ? `◉ lidar ${meta.points} rays · ${ageTxt}`
          : `lidar idle · last ${ageTxt}`}
      </span>
    </div>
  )
}
