import type { CagePlan, Placement, Size3 } from '../types'

export interface PackBox {
  skuId: string
  name: string
  w: number
  d: number
  h: number
  weight: number
}

export interface BinSpec extends Size3 {
  maxWeight: number
}

interface PlacedBox {
  ref: PackBox
  x: number
  y: number
  z: number
  w: number
  d: number
  h: number
  /** fraction of the base resting on the floor / cartons below (0-1) */
  support: number
}

const EPS = 1e-6
const CELL = 25 // height-map resolution (mm)
/** a carton must rest on at least this share of its base to be placeable */
export const SUPPORT_MIN = 0.6

function orientations(w: number, d: number, h: number): Array<[number, number, number]> {
  // all axis-aligned orientations — more flexibility packs denser
  const perms: Array<[number, number, number]> = [
    [w, d, h],
    [w, h, d],
    [d, w, h],
    [d, h, w],
    [h, w, d],
    [h, d, w],
  ]
  const seen = new Set<string>()
  const out: Array<[number, number, number]> = []
  for (const p of perms) {
    const key = p.join(',')
    if (!seen.has(key)) {
      seen.add(key)
      out.push(p)
    }
  }
  return out
}

interface Cage {
  placed: PlacedBox[]
  weight: number
  /** top surface height per floor cell (GW x GD) */
  heights: Float64Array
}

/** base support of `p` resting on the already-placed cartons in `below` */
function placementSupport(p: Placement, below: Placement[]): number {
  if (p.z <= EPS) return 1
  const sup = below.filter((b) => Math.abs(b.z + b.h - p.z) <= EPS)
  if (sup.length === 0) return 0
  const N = 8
  let hit = 0
  for (let i = 0; i < N; i++) {
    for (let j = 0; j < N; j++) {
      const px = p.x + ((i + 0.5) / N) * p.w
      const py = p.y + ((j + 0.5) / N) * p.d
      if (sup.some((b) => px >= b.x - EPS && px <= b.x + b.w + EPS && py >= b.y - EPS && py <= b.y + b.d + EPS)) {
        hit++
      }
    }
  }
  return hit / (N * N)
}

export interface PackResult {
  cages: CagePlan[]
  totalUtil: number
  itemCount: number
}

/**
 * Flat-surface 3D bin packing driven by a height map.
 *
 * A carton is only placed where the surface under its footprint is (mostly)
 * level, so every carton is physically supported — no floating placements.
 * Production delegates this to the server-side
 * `skjolber/3d-bin-container-packing` service (see services/endpoints.ts).
 */
export function packBoxes(boxes: PackBox[], bin: BinSpec): PackResult {
  const sorted = [...boxes].sort((a, b) => b.w * b.d * b.h - a.w * a.d * a.h)
  const itemCount = sorted.length

  const GW = Math.max(1, Math.ceil(bin.w / CELL))
  const GD = Math.max(1, Math.ceil(bin.d / CELL))
  const cages: Cage[] = []

  const openCage = (): Cage => {
    const c: Cage = { placed: [], weight: 0, heights: new Float64Array(GW * GD) }
    cages.push(c)
    return c
  }

  /** try to place one box in a cage; returns true on success */
  const tryPlace = (cage: Cage, box: PackBox): boolean => {
    if (cage.weight + box.weight > bin.maxWeight + EPS) return false
    let best: PlacedBox | null = null

    for (const [w, d, h] of orientations(box.w, box.d, box.h)) {
      const cw = Math.ceil(w / CELL - 1e-9)
      const cd = Math.ceil(d / CELL - 1e-9)
      if (cw > GW || cd > GD) continue
      for (let i = 0; i + cw <= GW; i++) {
        const x = i * CELL
        if (x + w > bin.w + EPS) break
        for (let j = 0; j + cd <= GD; j++) {
          const y = j * CELL
          if (y + d > bin.d + EPS) break

          let maxZ = 0
          for (let a = i; a < i + cw; a++) {
            const base = a * GD
            for (let b = j; b < j + cd; b++) {
              const hh = cage.heights[base + b]
              if (hh > maxZ) maxZ = hh
            }
          }
          if (maxZ + h > bin.h + EPS) continue

          // supported cells = those at the resting level (the rest are gaps)
          let supported = 0
          for (let a = i; a < i + cw; a++) {
            const base = a * GD
            for (let b = j; b < j + cd; b++) {
              if (cage.heights[base + b] >= maxZ - EPS) supported++
            }
          }
          const ratio = supported / (cw * cd)
          if (ratio < SUPPORT_MIN) continue

          const cand: PlacedBox = { ref: box, x, y, z: maxZ, w, d, h, support: ratio }
          const better =
            !best ||
            cand.z < best.z - EPS ||
            (Math.abs(cand.z - best.z) <= EPS &&
              (ratio > best.support + EPS ||
                (Math.abs(ratio - best.support) <= EPS &&
                  (cand.y < best.y - EPS || (Math.abs(cand.y - best.y) <= EPS && cand.x < best.x - EPS)))))
          if (better) best = cand
        }
      }
    }

    if (!best) return false
    const b = best
    const cw = Math.ceil(b.w / CELL - 1e-9)
    const cd = Math.ceil(b.d / CELL - 1e-9)
    const i0 = Math.round(b.x / CELL)
    const j0 = Math.round(b.y / CELL)
    const top = b.z + b.h
    for (let a = i0; a < i0 + cw; a++) {
      const base = a * GD
      for (let c = j0; c < j0 + cd; c++) cage.heights[base + c] = top
    }
    cage.placed.push(b)
    cage.weight += b.ref.weight
    return true
  }

  for (const box of sorted) {
    let placed = false
    for (const cage of cages) {
      if (tryPlace(cage, box)) {
        placed = true
        break
      }
    }
    if (!placed) {
      const cage = openCage()
      if (!tryPlace(cage, box)) {
        // force a floor placement so no carton is ever dropped
        const orient = orientations(box.w, box.d, box.h).find(
          ([w, d, h]) => w <= bin.w + EPS && d <= bin.d + EPS && h <= bin.h + EPS,
        )
        if (orient) {
          const [w, d, h] = orient
          cage.placed.push({ ref: box, x: 0, y: 0, z: 0, w, d, h, support: 1 })
          cage.weight += box.weight
          const cw = Math.ceil(w / CELL - 1e-9)
          const cd = Math.ceil(d / CELL - 1e-9)
          for (let a = 0; a < cw; a++) {
            const base = a * GD
            for (let c = 0; c < cd; c++) cage.heights[base + c] = h
          }
        }
      }
    }
  }

  const binVolume = bin.w * bin.d * bin.h
  const plans: CagePlan[] = cages.map((cage, i) => {
    const placements: Placement[] = cage.placed.map((b) => ({
      seq: 0,
      skuId: b.ref.skuId,
      name: b.ref.name,
      x: b.x,
      y: b.y,
      z: b.z,
      w: b.w,
      d: b.d,
      h: b.h,
      weight: b.ref.weight,
      support: b.support,
    }))
    // Robot-feasible placement order: bottom-up, deepest-first (the arm works
    // from the cage opening at y = 0), then left-to-right — so the arm never has
    // to reach over cartons it has already placed.
    placements.sort((a, b) => a.z - b.z || b.y - a.y || a.x - b.x)
    placements.forEach((p, idx) => {
      p.seq = idx + 1
    })
    // measure support against the cartons placed earlier in the sequence, so the
    // reported value matches what the animation shows
    placements.forEach((p, idx) => {
      p.support = idx === 0 ? 1 : placementSupport(p, placements.slice(0, idx))
    })
    const used = placements.reduce((acc, p) => acc + p.w * p.d * p.h, 0)
    const fillHeight = placements.reduce((acc, p) => Math.max(acc, p.z + p.h), 0)
    return {
      cageIndex: i,
      placements,
      volumeUtil: used / binVolume,
      loadWeight: cage.weight,
      fillHeight,
    }
  })

  const totalUsed = plans.reduce(
    (acc, c) => acc + c.placements.reduce((s, p) => s + p.w * p.d * p.h, 0),
    0,
  )
  const totalUtil = totalUsed / (binVolume * Math.max(plans.length, 1))

  return { cages: plans, totalUtil, itemCount }
}
