import { useMemo, useState } from 'react'
import { useApp } from '../store/AppStore'
import { Badge, Card, PageHeader } from '../components/ui'
import { kg, volumeLitres } from '../lib/format'

const emptyForm = { name: '', category: '', w: 400, d: 300, h: 200, weight: 10 }

export function Goods() {
  const { skus, orders, addSku, removeSku } = useApp()
  const [form, setForm] = useState({ ...emptyForm })
  const [error, setError] = useState<string | null>(null)
  const [justAdded, setJustAdded] = useState<string | null>(null)

  const usedSkus = useMemo(() => {
    const used = new Set<string>()
    for (const o of orders) for (const it of o.items) used.add(it.skuId)
    return used
  }, [orders])

  const set = (patch: Partial<typeof form>) => setForm((f) => ({ ...f, ...patch }))

  function submit() {
    if (!form.name.trim()) {
      setError('Name is required.')
      return
    }
    if (form.w <= 0 || form.d <= 0 || form.h <= 0) {
      setError('Dimensions must be greater than zero.')
      return
    }
    if (form.weight <= 0) {
      setError('Weight must be greater than zero.')
      return
    }
    setError(null)
    const sku = addSku({
      name: form.name.trim(),
      category: form.category.trim() || 'Uncategorised',
      size: { w: form.w, d: form.d, h: form.h },
      weight: form.weight,
    })
    setJustAdded(sku.id)
    setForm({ ...emptyForm })
  }

  return (
    <>
      <PageHeader
        title="Goods"
        desc="Enter goods parameters (name, category, dimensions, weight). New goods become selectable in Orders and feed the packing engine."
      />

      <div className="grid split">
        <Card title="Add goods" hint="dimensions in mm · weight in kg">
          <div className="grid cols-2">
            <div className="field">
              <label>Name</label>
              <input
                value={form.name}
                placeholder="e.g. Frozen Dumplings 1kg×10"
                onChange={(e) => set({ name: e.target.value })}
              />
            </div>
            <div className="field">
              <label>Category</label>
              <input
                value={form.category}
                placeholder="e.g. Frozen dim sum"
                onChange={(e) => set({ category: e.target.value })}
              />
            </div>
          </div>

          <div className="grid cols-4" style={{ marginTop: 12 }}>
            <div className="field">
              <label>Width (mm)</label>
              <input type="number" min={1} value={form.w} onChange={(e) => set({ w: Number(e.target.value) })} />
            </div>
            <div className="field">
              <label>Depth (mm)</label>
              <input type="number" min={1} value={form.d} onChange={(e) => set({ d: Number(e.target.value) })} />
            </div>
            <div className="field">
              <label>Height (mm)</label>
              <input type="number" min={1} value={form.h} onChange={(e) => set({ h: Number(e.target.value) })} />
            </div>
            <div className="field">
              <label>Weight (kg)</label>
              <input
                type="number"
                min={0.1}
                step={0.1}
                value={form.weight}
                onChange={(e) => set({ weight: Number(e.target.value) })}
              />
            </div>
          </div>

          {error && (
            <p style={{ color: 'var(--amber)', fontSize: 12.5, margin: '10px 0 0' }}>{error}</p>
          )}

          <div className="row" style={{ marginTop: 14 }}>
            <button className="btn primary" onClick={submit}>
              + Add goods
            </button>
            <span className="faint" style={{ fontSize: 12 }}>
              Volume {volumeLitres(form.w * form.d * form.h)} · {form.w}×{form.d}×{form.h} mm
            </span>
          </div>
        </Card>

        <Card title="Preview" hint="carton block">
          <div
            style={{
              height: 180,
              borderRadius: 10,
              border: '1px solid var(--border)',
              background: 'radial-gradient(300px 160px at 50% 10%, #101923, #0a0f15)',
              display: 'grid',
              placeItems: 'center',
            }}
          >
            <svg width="220" height="150" viewBox="0 0 220 150">
              {(() => {
                const maxDim = Math.max(form.w, form.d, form.h, 1)
                const sx = (90 * form.w) / maxDim
                const sy = (60 * form.d) / maxDim
                const sz = (90 * form.h) / maxDim
                const ox = 60
                const oy = 120
                const a = (x: number, y: number, z: number) => ({
                  x: ox + x - y * 0.5,
                  y: oy - z - y * 0.35,
                })
                const p = {
                  b0: a(0, 0, 0),
                  b1: a(sx, 0, 0),
                  b2: a(sx, sy, 0),
                  b3: a(0, sy, 0),
                  t4: a(0, 0, sz),
                  t5: a(sx, 0, sz),
                  t6: a(sx, sy, sz),
                  t7: a(0, sy, sz),
                }
                const poly = (pts: Array<{ x: number; y: number }>, fill: string) => (
                  <polygon points={pts.map((q) => `${q.x},${q.y}`).join(' ')} fill={fill} stroke="#22d3ee" strokeWidth="1" />
                )
                return (
                  <>
                    {poly([p.b3, p.b2, p.t6, p.t7], '#0e2a33')}
                    {poly([p.b1, p.b2, p.t6, p.t5], '#123843')}
                    {poly([p.t4, p.t5, p.t6, p.t7], '#17495a')}
                  </>
                )
              })()}
            </svg>
          </div>
          <div className="stack" style={{ marginTop: 12, fontSize: 12.5 }}>
            <div className="row between">
              <span className="muted">Name</span>
              <span>{form.name || '—'}</span>
            </div>
            <div className="row between">
              <span className="muted">Volume</span>
              <span>{volumeLitres(form.w * form.d * form.h)}</span>
            </div>
            <div className="row between">
              <span className="muted">Weight</span>
              <span>{kg(form.weight)}</span>
            </div>
          </div>
        </Card>
      </div>

      {justAdded && (
        <div className="stale-warning" style={{ color: 'var(--green)', borderColor: 'rgba(52,211,153,0.25)', background: 'rgba(52,211,153,0.08)' }}>
          ✓ Goods <span className="mono">{justAdded}</span> added — pick it in Orders (“New order”).
        </div>
      )}

      <Card title="Goods catalogue" hint={`${skus.length} items`}>
        <div className="table-scroll"><table className="data">
          <thead>
            <tr>
              <th>SKU</th>
              <th>Name</th>
              <th>Category</th>
              <th>Size (w×d×h mm)</th>
              <th>Volume</th>
              <th>Weight</th>
              <th>Used by</th>
              <th style={{ textAlign: 'right' }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {skus.map((s) => (
              <tr key={s.id}>
                <td className="tag">{s.id}</td>
                <td>{s.name}</td>
                <td className="muted">{s.category}</td>
                <td className="mono muted">
                  {s.size.w}×{s.size.d}×{s.size.h}
                </td>
                <td className="muted">{volumeLitres(s.size.w * s.size.d * s.size.h)}</td>
                <td className="muted">{kg(s.weight)}</td>
                <td>
                  {usedSkus.has(s.id) ? <Badge tone="cyan">in orders</Badge> : <span className="faint">—</span>}
                </td>
                <td>
                  <div className="row" style={{ justifyContent: 'flex-end' }}>
                    <button
                      className="btn sm danger"
                      disabled={usedSkus.has(s.id)}
                      title={usedSkus.has(s.id) ? 'In use by an order' : 'Remove'}
                      onClick={() => removeSku(s.id)}
                    >
                      Remove
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {skus.length === 0 && (
              <tr>
                <td colSpan={8} className="empty">
                  No goods yet — add one above.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        </div>
      </Card>
    </>
  )
}
