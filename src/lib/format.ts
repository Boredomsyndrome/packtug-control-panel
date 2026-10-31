export function pct(x: number, digits = 1): string {
  return `${(x * 100).toFixed(digits)}%`
}

export function kg(x: number): string {
  return `${x.toFixed(x < 10 ? 1 : 0)} kg`
}

export function clock(iso?: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function timeAgo(iso?: string): string {
  if (!iso) return '—'
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  return `${Math.floor(s / 3600)}h ago`
}

export function duration(sec?: number): string {
  if (sec === undefined) return '—'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}m ${String(s).padStart(2, '0')}s`
}

export function volumeLitres(mm3: number): string {
  return `${(mm3 / 1e6).toFixed(0)} L`
}

/** Stable hue (0-360) for a SKU id — shared by the 3D view and tables. */
export function hueForSku(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360
  return h
}
