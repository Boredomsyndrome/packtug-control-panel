import type { CageGeometry } from '../services/api'
import type { Placement } from '../types'

export const CAGE_STORAGE_KEY = 'packtug.cageGeometry'
export const ARM_TARGET_STORAGE_KEY = 'packtug.armTarget'

export type TargetReference = 'web' | 'center'

export interface ArmTargetFromPacking {
  x: number
  y: number
  z: number
  placementSeq: number
  skuId: string
  reference: TargetReference
}

const WEB_DEPTH_MM = 20
const WEB_HEIGHT_MM = 87.5

export function placementToLocalTarget(
  placement: Placement,
  reference: TargetReference = 'web',
): { x: number; y: number; z: number } {
  return {
    x: placement.x + placement.w / 2,
    y: reference === 'web' ? placement.y + WEB_DEPTH_MM : placement.y + placement.d / 2,
    z: reference === 'web' ? placement.z + WEB_HEIGHT_MM : placement.z + placement.h / 2,
  }
}

export function placementToArmTarget(
  placement: Placement,
  cage: CageGeometry,
  reference: TargetReference = 'web',
): { x: number; y: number; z: number } {
  const local = placementToLocalTarget(placement, reference)
  const yaw = (cage.yaw * Math.PI) / 180
  const x = local.x / 1000
  const y = local.y / 1000

  return {
    x: cage.x + x * Math.cos(yaw) - y * Math.sin(yaw),
    y: cage.y + x * Math.sin(yaw) + y * Math.cos(yaw),
    z: cage.z + local.z / 1000,
  }
}
