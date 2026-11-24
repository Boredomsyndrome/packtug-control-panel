// v3.7 (merge) CS-B4: topbar badge logic, pure + unit-tested.
//
// The old topbar chip hardcoded "AGV DATA · mock" because it borrowed the
// ARM bridge's mock flag — while the Fleet feed was actually LIVE through
// the fused hub. Rule: AGV chip reflects whether OUR hub's fleet endpoint
// answers; the arm bridge gets its OWN chip (mock state included).

export type ChipTone = 'green' | 'red' | 'amber'
export interface Chip {
  label: string
  tone: ChipTone
}

/** AGV/fleet chip from the /api/vehicle probe (the bridge's live hub
 * proxy). `reachable` = the request completed with ok JSON at all;
 * robots[] inside may still be offline — that is a FLEET state, not a
 * data-source state. */
export function vehicleChip(reachable: boolean): Chip {
  return reachable
    ? { label: 'AGV DATA · live', tone: 'green' }
    : { label: 'AGV DATA · offline', tone: 'red' }
}

/** Arm-bridge chip from the bridge's own /api/health {status, mock}. */
export function armChip(health: { status?: string; mock?: boolean } | null): Chip {
  if (!health || health.status !== 'ok') return { label: 'bridge · offline', tone: 'red' }
  return health.mock
    ? { label: 'bridge · mock', tone: 'amber' }
    : { label: 'bridge · live', tone: 'green' }
}


/** WHEELTEC 6S pack: at 22.4 V or below, charge immediately. */
export const WHEELTEC_CHARGE_V = 22.4
/** 22.4–22.7 V is the low-voltage warning band. */
export const WHEELTEC_WARN_V = 22.7

export type BatteryWarning = {
  level: 'danger' | 'critical'
  label: string
}

export function wheeltecBatteryWarning(
  voltage: number | null | undefined,
): BatteryWarning | null {
  if (voltage == null || !Number.isFinite(voltage)) return null
  if (voltage <= WHEELTEC_CHARGE_V) {
    return { level: 'critical', label: 'CHARGE NOW · ≤22.4 V' }
  }
  if (voltage <= WHEELTEC_WARN_V) {
    return { level: 'danger', label: 'LOW VOLTAGE · 22.4–22.7 V' }
  }
  return null
}
