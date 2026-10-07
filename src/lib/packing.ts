import type { CagePlan, Placement, Size3, Vec3 } from '../types'

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
}

const EPS = 1e-6

function orientations(w: number, d: number, h: number): Array<[number, number, number]> {
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

function fits(box: PlacedBox, bin: BinSpec): boolean {
  return (
    box.x >= -EPS &&
    box.y >= -EPS &&
    box.z >= -EPS &&
    box.x + box.w <= bin.w + EPS &&
    box.y + box.d <= bin.d + EPS &&
    box.z + box.h <= bin.h + EPS
  )
}

function overlaps(a: PlacedBox, b: PlacedBox): boolean {
  return (
    a.x < b.x + b.w - EPS &&
    a.x + a.w > b.x + EPS &&
    a.y < b.y + b.d - EPS &&
    a.y + a.d > b.y + EPS &&
    a.z < b.z + b.h - EPS &&
    a.z + a.h > b.z + EPS
  )
}

interface OpenCage {
  placed: PlacedBox[]
  points: Vec3[]
  weight: number
}

export interface PackResult {
  cages: CagePlan[]
  totalUtil: number
  itemCount: number
}

/**
 * Extreme-point first-fit 3D bin packing.
 *
 * Production delegates this to the server-side
 * `skjolber/3d-bin-container-packing` service (see services/endpoints.ts).
 * This client implementation lets the prototype compute and visualise a
 * packing plan with no hardware or backend.
 */
export function packBoxes(boxes: PackBox[], bin: BinSpec): PackResult {
  const sorted = [...boxes].sort((a, b) => b.w * b.d * b.h - a.w * a.d * a.h)
  const cages: OpenCage[] = []
  const itemCount = sorted.length

  const openCage = (): OpenCage => {
    const c: OpenCage = { placed: [], points: [{ x: 0, y: 0, z: 0 }], weight: 0 }
    cages.push(c)
    return c
  }
  openCage()

  for (const box of sorted) {
    let placedAny = false

    for (let ci = 0; ci < cages.length && !placedAny; ci++) {
      const cage = cages[ci]
      const indexOrder = cage.points
        .map((_, i) => i)
        .sort((i, j) => {
          const a = cage.points[i]
          const b = cage.points[j]
          return a.z - b.z || a.y - b.y || a.x - b.x
        })

      for (const pi of indexOrder) {
        if (placedAny) break
        const p = cage.points[pi]
        for (const [w, d, h] of orientations(box.w, box.d, box.h)) {
          const cand: PlacedBox = { ref: box, x: p.x, y: p.y, z: p.z, w, d, h }
          if (!fits(cand, bin)) continue
          if (cage.weight + box.weight > bin.maxWeight + EPS) continue
          let clash = false
          for (const q of cage.placed) {
            if (overlaps(cand, q)) {
              clash = true
              break
            }
          }
          if (clash) continue

          cage.placed.push(cand)
          cage.weight += box.weight
          cage.points.splice(pi, 1)
          cage.points.push(
            { x: cand.x + w, y: cand.y, z: cand.z },
            { x: cand.x, y: cand.y + d, z: cand.z },
            { x: cand.x, y: cand.y, z: cand.z + h },
          )
          placedAny = true
          break
        }
      }
    }

    if (!placedAny) {
      const cage = openCage()
      const [w, d, h] = orientations(box.w, box.d, box.h)[0]
      cage.placed.push({ ref: box, x: 0, y: 0, z: 0, w, d, h })
      cage.weight += box.weight
      cage.points.splice(0, 1)
      cage.points.push({ x: w, y: 0, z: 0 }, { x: 0, y: d, z: 0 }, { x: 0, y: 0, z: h })
    }
  }

  const binVolume = bin.w * bin.d * bin.h
  const plans: CagePlan[] = cages.map((cage, i) => {
    const placements: Placement[] = cage.placed.map((b) => ({
      skuId: b.ref.skuId,
      name: b.ref.name,
      x: b.x,
      y: b.y,
      z: b.z,
      w: b.w,
      d: b.d,
      h: b.h,
      weight: b.ref.weight,
    }))
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
