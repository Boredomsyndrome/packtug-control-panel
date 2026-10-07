import type { ReactNode } from 'react'

export type Tone = 'green' | 'amber' | 'red' | 'cyan' | 'violet' | 'grey'

export function Badge({ tone = 'grey', dot = false, children }: { tone?: Tone; dot?: boolean; children: ReactNode }) {
  return (
    <span className={`badge ${tone}`}>
      {dot && <span className="dot" />}
      {children}
    </span>
  )
}

export function Progress({ value, tone }: { value: number; tone?: 'green' | 'amber' }) {
  const pctv = Math.max(0, Math.min(1, value)) * 100
  return (
    <div className={`progress ${tone ?? ''}`}>
      <span style={{ width: `${pctv}%` }} />
    </div>
  )
}

export function Card({
  title,
  hint,
  right,
  children,
  className,
}: {
  title?: ReactNode
  hint?: ReactNode
  right?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`card ${className ?? ''}`}>
      {(title || right) && (
        <div className="card-head">
          <div>
            {title && <h3>{title}</h3>}
            {hint && <div className="hint">{hint}</div>}
          </div>
          {right}
        </div>
      )}
      {children}
    </section>
  )
}

export function KpiCard({
  label,
  value,
  foot,
  tone = 'cyan',
}: {
  label: string
  value: ReactNode
  foot?: ReactNode
  tone?: Tone
}) {
  const color =
    tone === 'green' ? 'var(--green)' : tone === 'amber' ? 'var(--amber)' : tone === 'red' ? 'var(--red)' : 'var(--accent)'
  return (
    <div className="card">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value" style={{ color }}>
        {value}
      </div>
      {foot && <div className="kpi-foot">{foot}</div>}
    </div>
  )
}

export function PageHeader({ title, desc, actions }: { title: string; desc?: string; actions?: ReactNode }) {
  return (
    <div className="row between">
      <div>
        <h2 className="page-title">{title}</h2>
        {desc && <p className="page-desc">{desc}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>
}
