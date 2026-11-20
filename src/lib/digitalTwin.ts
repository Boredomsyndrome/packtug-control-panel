import type { CageGeometry } from '../services/api'
import type { Placement } from '../types'

export const CAGE_STORAGE_KEY = 'packtug.cageGeometry'
export const ARM_TARGET_STORAGE_KEY = 'packtug.armTarget'

export type TargetReference = 'front' | 'center'

export function placementOrientation(cage: CageGeometry) {
  return { roll: 0, pitch: 0, yaw: ((cage.yaw + 270) % 360 + 360) % 360 - 180 }
}

export interface ArmTargetFromPacking {
  x: number
  y: number
  z: number
  placementSeq: number
  skuId: string
  reference: TargetReference
}

export function placementToLocalTarget(
  placement: Placement,
  reference: TargetReference = 'front',
): { x: number; y: number; z: number } {
  return {
    x: placement.x + placement.w / 2,
    y: reference === 'front' ? placement.y : placement.y + placement.d / 2,
    z: placement.z + placement.h / 2,
  }
}

export function placementToArmTarget(
  placement: Placement,
  cage: CageGeometry,
  reference: TargetReference = 'front',
  packingWidth = cage.width,
): { x: number; y: number; z: number } {
  const local = placementToLocalTarget(placement, reference)
  const yaw = (cage.yaw * Math.PI) / 180
  // Packing uses the front-left floor corner. The placement x/y coordinates
  // are expressed in the packing bin, so centre them with the *packing* width;
  // the taught cage origin is still used for the world transform.
  const x = local.x / 1000 - packingWidth / 2
  const y = local.y / 1000

  return {
    x: cage.x + x * Math.cos(yaw) - y * Math.sin(yaw),
    y: cage.y + x * Math.sin(yaw) + y * Math.cos(yaw),
    z: cage.z + local.z / 1000,
  }
}

export function placementTargetIssues(
  placement: Placement,
  cage: CageGeometry,
  reference: TargetReference = 'front',
): string[] {
  const issues: string[] = []
  const local = placementToLocalTarget(placement, reference)
  const axes = [
    ['width', placement.x, placement.w, cage.width, local.x],
    ['depth', placement.y, placement.d, cage.depth, local.y],
    ['height', placement.z, placement.h, cage.height, local.z],
  ] as const
  for (const [name, start, size, extent, target] of axes) {
    const limit = extent * 1000
    if (![start, size, limit, target].every(Number.isFinite) || start < 0 || size <= 0 || limit <= 0 || start + size > limit + 1e-6) {
      issues.push(`Box exceeds the taught cage ${name}; check packing dimensions and cage teaching.`)
    }
    // The front is open; only the side, back, floor and ceiling walls need clearance.
    const gap = name === 'depth' ? limit - target : Math.min(target, limit - target)
    if (gap + 1e-6 < cage.clearance_m * 1000) {
      issues.push(`Target ${name} clearance ${gap.toFixed(0)} mm < ${(cage.clearance_m * 1000).toFixed(0)} mm.`)
    }
  }
  if (reference === 'front' && local.y < 0) {
    issues.push('The box front face must not be outside the cage opening (Y ≥ 0).')
  }
  return issues
}
