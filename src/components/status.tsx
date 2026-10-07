import type { AgvStatus, CageState, MissionStatus, MissionType, OrderPriority, OrderStatus, ZoneKind } from '../types'
import type { Tone } from './ui'

interface Meta {
  label: string
  tone: Tone
}

export const ORDER_STATUS: Record<OrderStatus, Meta> = {
  pending: { label: 'Pending', tone: 'grey' },
  planned: { label: 'Planned', tone: 'cyan' },
  picking: { label: 'Picking', tone: 'amber' },
  packed: { label: 'Packed', tone: 'violet' },
  transferring: { label: 'Transferring', tone: 'cyan' },
  done: { label: 'Done', tone: 'green' },
  exception: { label: 'Exception', tone: 'red' },
}

export const PRIORITY: Record<OrderPriority, Meta> = {
  low: { label: 'Low', tone: 'grey' },
  normal: { label: 'Normal', tone: 'cyan' },
  high: { label: 'High', tone: 'red' },
}

export const AGV_STATUS: Record<AgvStatus, Meta> = {
  idle: { label: 'Idle', tone: 'grey' },
  navigating: { label: 'Navigating', tone: 'cyan' },
  picking: { label: 'Picking', tone: 'amber' },
  towing: { label: 'Towing', tone: 'violet' },
  charging: { label: 'Charging', tone: 'green' },
  fault: { label: 'Fault', tone: 'red' },
  offline: { label: 'Offline', tone: 'grey' },
}

export const MISSION_STATUS: Record<MissionStatus, Meta> = {
  queued: { label: 'Queued', tone: 'grey' },
  running: { label: 'Running', tone: 'cyan' },
  paused: { label: 'Paused', tone: 'amber' },
  done: { label: 'Done', tone: 'green' },
  failed: { label: 'Failed', tone: 'red' },
}

export const MISSION_TYPE: Record<MissionType, string> = {
  pick: 'Pick',
  pack: 'Pack',
  transport: 'Transport',
  charge: 'Charge',
  handover: 'Handover',
}

export const CAGE_STATE: Record<CageState, Meta> = {
  empty: { label: 'Empty', tone: 'grey' },
  loading: { label: 'Loading', tone: 'amber' },
  sealed: { label: 'Sealed', tone: 'violet' },
  'in-transit': { label: 'In transit', tone: 'cyan' },
  staged: { label: 'Staged', tone: 'green' },
}

export const ZONE_KIND: Record<ZoneKind, string> = {
  'cold-store': 'Cold store',
  staging: 'Staging',
  dispatch: 'Dispatch',
  charging: 'Charging',
}

export function batteryTone(level: number): Tone {
  if (level <= 20) return 'red'
  if (level <= 45) return 'amber'
  return 'green'
}
